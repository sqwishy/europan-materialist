#![allow(unused_variables)]
#![allow(unused_imports)]
#![allow(dead_code)]
#![allow(unreachable_code)]

use std::sync::Arc;
use std::time::Duration;

use anyhow::Context;

use reqwest::Url;

use tokio::{
    sync::{Mutex, Notify},
    task,
};

use kanal::{AsyncReceiver as Receiver, AsyncSender as Sender};

pub(crate) mod ansi;
pub(crate) mod logging;
pub(crate) mod no_args;

pub const BARO_APPID: &'static str = "602960";

#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[serde(default, rename_all = "kebab-case", deny_unknown_fields)]
pub struct Config {
    pub www: std::net::SocketAddr,
    pub db: String,
    pub steamcmd_url: Url,
    pub steamcmd_concurrency: u8,
    pub steamcommunity_url: Url,
    pub steamcommunity_concurrency: u8,
    #[serde(with = "crate::no_args::opt_duration_ms")]
    pub steamcommunity_read_timeout: Option<Duration>,
    pub podman_unix: String,
    pub podman_concurrency: u8,
    pub user_agent: String,
    pub publish_image: String,
    pub build_image: String,
    /* A volume containing a file named `cloudflare` that looks like;
     * CLOUDFLARE_ACCOUNT_ID=...
     * CLOUDFLARE_API_TOKEN=... */
    pub secrets_volume: String,
    pub deploy_site: String,
    // /// user accessible url to deploy_site
    // pub deploy_url: String,
    #[serde(with = "crate::no_args::duration_ms")]
    pub wait_on_publish_poll_interval: Duration,
    pub response_headers: crate::no_args::headers::ExtraHeaders,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            www: "127.0.0.1:8847".parse().unwrap(),
            db: "/tmp/materialist-rs.sqlite".to_string(),
            steamcmd_url: "http://localhost:8888/".parse().unwrap(),
            steamcmd_concurrency: 3,
            steamcommunity_url: "https://steamcommunity.com/".parse().unwrap(),
            steamcommunity_concurrency: 4,
            steamcommunity_read_timeout: Duration::from_secs(30).into(),
            podman_unix: "/run/user/1000/podman/podman.sock".parse().unwrap(),
            podman_concurrency: 8,
            user_agent: "europan-materialist/0 (materialist.pages.dev)".to_string(),
            publish_image: "splicer-publish".to_string(),
            build_image: "splicer-build".to_string(),
            secrets_volume: "materialist-secrets".to_string(),
            deploy_site: "materialist-next".to_string(),
            // deploy_url: "https://materialist-next.pages.dev/".to_string(),
            wait_on_publish_poll_interval: Duration::from_millis(500),
            response_headers: no_args::headers::ExtraHeaders(vec![
                (
                    axum::http::header::HeaderName::from_static(
                        "access-control-allow-origin",
                    ),
                    axum::http::header::HeaderValue::from_static("*"),
                ),
                (
                    axum::http::header::HeaderName::from_static(
                        "access-control-allow-methods",
                    ),
                    axum::http::header::HeaderValue::from_static("POST, GET, OPTIONS"),
                ),
            ]),
        }
    }
}

fn main() {
    let config: Config = {
        let mut no_args = no_args::from_argv("materialist", "materialist.toml");
        let _ = no_args.canonicalize();
        butt!("using config"; "path" => no_args.path().display());
        match no_args.read_and_parse() {
            Ok(config) => config,
            Err(err) => {
                crit!("invalid config"; "err" => logging::dbg(&err));
                std::process::exit(1);
            }
        }
    };

    let rt = match tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
    {
        Ok(rt) => rt,
        Err(err) => {
            crit!("failed to set up runtime"; "err" => err);
            return;
        }
    };

    match rt.block_on(async_main(config)) {
        Ok(()) => (),
        Err(err) => {
            crit!("{:?}", err);
        }
    }
}

#[derive(Debug)]
pub struct SystemMsg;

async fn async_main(config: Config) -> anyhow::Result<()> {
    let cfg = Arc::new(config);

    let (sys_s, sys_r) = kanal::bounded_async::<SystemMsg>(0);

    let listener = tokio::net::TcpListener::bind(&cfg.www)
        .await
        .context("bind to listen address")?;

    let db_connection = db::connect(&cfg.db).context("open database")?;

    let (db_s, db_r) = kanal::bounded_async(3);

    let podman: podman::Client = httpreq::builder()
        .user_agent(&cfg.user_agent)
        .unix_socket(&cfg.podman_unix)
        .limit(cfg.podman_concurrency)
        .build()?
        .into();

    let steamcmd: steamcmd::Client = httpreq::builder()
        .user_agent(&cfg.user_agent)
        .limit(cfg.steamcmd_concurrency)
        .build()?
        .into();

    let steamweb: steamweb::Client = httpreq::builder()
        .user_agent(&cfg.user_agent)
        .limit(cfg.steamcommunity_concurrency)
        .read_timeout(cfg.steamcommunity_read_timeout)
        .build()?
        .into();

    let (pub_s, pub_r) = kanal::bounded_async(128);

    let www = www::Server {
        listener,
        sys_r: sys_r.clone(),
        db: db::Client { s: db_s },
        cfg: Arc::clone(&cfg),
        podman: podman.clone(),
        steamcmd,
        steamweb,
        publish: pub_s,
    };

    let publish = publish::Server {
        sys_r: sys_r.clone(),
        pub_r,
        db: www.db.clone(),
        cfg: Arc::clone(&cfg),
        podman,
    };

    info!("listening"; "addr" => www.listener.local_addr().as_ref().unwrap_or(&cfg.www));

    let (db_thread_s, db_thread_r) = kanal::bounded_async::<()>(0);
    let db_thread = std::thread::Builder::new()
        .name("materialist-db".to_string())
        .spawn(move || {
            let _ = db_thread_s /* drop signals that this thread has quit */;
            let mut db_daemon = db::Daemon { db: db_connection, r: db_r.to_sync() };
            db_daemon.run_forever()
        })
        .context("start database thread")?;

    let mut www_task = Some(task::spawn(www.run_forever()));
    let mut pub_task = Some(task::spawn(publish.run_forever()));
    let mut db_task = Some(task::spawn(async move {
        tokio::select! {
            _ = sys_r.recv() => (),
            _ = db_thread_r.recv() => (),
        }
    }));

    let mut sys_s = Some(sys_s);

    while www_task.is_some() || pub_task.is_some() || db_task.is_some() {
        tokio::select! {
            biased;

            res = once_task(&mut db_task), if db_task.is_some() => match res {
                Ok(()) => butt!("db exit"),
                Err(joinerr) => crit!("db panic?"; "err" => logging::err(joinerr)),
            },

            res = once_task(&mut pub_task), if pub_task.is_some() => match res {
                Ok(Ok(())) => butt!("publish exit"),
                Ok(Err(err)) => crit!("publish failed"; "err" => logging::err(err)),
                Err(joinerr) => crit!("publish panic?"; "err" => logging::err(joinerr)),
            },

            res = once_task(&mut www_task), if www_task.is_some() => match res {
                Ok(Ok(())) => butt!("www exit"),
                Ok(Err(err)) => crit!("www failed"; "err" => logging::err(err)),
                Err(joinerr) => crit!("www panic?"; "err" => logging::err(joinerr)),
            },

            sig = tokio::signal::ctrl_c() => warn!("ctrl-c"),
        };

        if sys_s.take().is_some() {
            butt!("shutting down");
        };
    }

    if let Err(err) = db_thread.join() {
        crit!("database thread panicked; I guess I will too!");
        std::panic::resume_unwind(err);
    }

    return Ok(());

    async fn once_task<T>(
        o: &mut Option<tokio::task::JoinHandle<T>>,
    ) -> Result<T, tokio::task::JoinError> {
        match o {
            Some(h) => {
                let r = h.await;
                *o = None;
                r
            }
            None => std::future::pending().await,
        }
    }
}

pub(crate) mod publish {
    use std::sync::Arc;

    use kanal::{AsyncReceiver as Receiver, AsyncSender as Sender};

    use anyhow::Context;

    use serde_json::json;

    use reqwest::{
        header::{HeaderValue, CONTENT_TYPE},
        StatusCode,
    };

    use crate::{
        butt, crit, db, logging, oof,
        podman::{self, traits::*},
        warn,
        www::{traits::*, APPLICATION_XTAR},
    };

    #[derive(Debug)]
    pub enum Msg {
        Publish { reply: Sender<PublishReply> },
    }

    pub struct PublishReply {
        pub pk: i64,
    }

    pub struct Server {
        pub sys_r: Receiver<super::SystemMsg>,
        pub pub_r: Receiver<Msg>,
        pub cfg: Arc<super::Config>,
        pub db: db::Client,
        pub podman: podman::Client,
    }

    impl Server {
        pub async fn run_forever(self) -> std::io::Result<()> {
            loop {
                tokio::select! {
                    biased;

                    res = self.pub_r.recv() => match res {
                        Err(_) => break,
                        Ok(msg) => self.start_publish(msg).await,
                    },

                    _ = self.sys_r.recv() => break,
                }
            }

            Ok(())
        }

        async fn start_publish(&self, first: Msg) {
            // use std::time::Duration;
            // use tokio::time::sleep;
            // let mut deadline = sleep(Duration::from_millis(300));

            let mut msgs = vec![first];
            let _ = self.pub_r.drain_into(&mut msgs);

            let (pk, included) = match self.publish().await {
                Ok(v) => v,
                Err(err) => {
                    crit!("failed publish"; "err" => logging::err(err));
                    return;
                }
            };

            butt!("published";
                  "pk" => pk,
                  "included_builds" => logging::dbg(&included));

            for msg in msgs {
                match msg {
                    Msg::Publish { reply } => {
                        let _ = reply.send(PublishReply { pk }).await;
                    }
                }
            }
        }

        async fn publish(&self) -> anyhow::Result<(i64, Vec<i64>)> {
            let new_publish = self
                .db
                .new_publish()
                .await
                .with_context(|| oof![s ~ "db send"])?
                .recv()
                .await
                .ok()
                .with_context(|| oof![s ~ "db recv"])?
                .with_context(|| oof![s ~ "new_publish"])?;

            let pk = new_publish.pk;
            let included = new_publish
                .fragments
                .iter()
                .map(|f| f.build)
                .collect::<Vec<_>>();

            // if new_publish.fragments.is_empty() {
            //     return anyhow::anyhow!("no builds found, refusing to publish")?;
            // }

            let podman = self
                .podman
                .acquire()
                .await
                .with_context(|| oof![s ~ "acquire podman"])?;

            let create = json!({
                "name": format!("materialist-publish-{}", pk),
                "image": &self.cfg.publish_image,
                "image_volume_mode": "tmpfs",
                "env": {
                    "CI": "1",
                    "PROJECT_NAME": &self.cfg.deploy_site,
                },
                "mounts": [{
                    "Type": "tmpfs",
                    "Destination": "/publish/web/assets/bundles",
                }],
                "terminal": true,
                "volumes": [{
                    "Name": &self.cfg.secrets_volume,
                    "SubPath": "cloudflare",
                    "Dest": "/run/secrets/cloudflare",
                }],
            });

            let container_id = podman
                .post("http://p/v6.0.0/libpod/containers/create")
                .json(&create)
                .expect_status(StatusCode::CREATED)
                .await
                .parse_json::<podman::CreateResponse>()
                .with_context(|| {
                    oof![s ~ "libpod/containers/create",
                         req ~ create.clone()]
                })
                .map(|podman::CreateResponse { Id, Warnings }| {
                    if !Warnings.is_empty() {
                        warn!("libpod/containers/create";
                              "warnings" => logging::dbg(Warnings),
                              "req" => &create);
                    }
                    Id
                })?;

            butt!("libpod/containers/create";
                  "req" => &create, "id" => &container_id);

            let res = self
                ._publish_from_container(new_publish, &podman, &container_id)
                .await
                .with_context(|| oof![pk ~ pk, container ~ container_id.to_string()]);

            let delete = podman
                .delete(format!(
                    "http://p/v6.0.0/libpod/\
                        containers/{container_id}?force=1"
                ))
                .send_and_read_json()
                .await
                .expect_status(StatusCode::OK);

            if let Err(err) = delete {
                warn!("failed libpod/containers/delete";
                      "container" => &container_id,
                      "err" => logging::err(err));
            }

            /* TODO
             * this will return if res is an Err(_)
             * but shouldn't this save a PublishResult with exit code -1 or something? */
            let (output, exit_code) = res?;
            let public_url = format!("https://{}.pages.dev/", self.cfg.deploy_site);
            let result = db::PublishResult { pk, exit_code, output, public_url };
            self.db
                .save_publish_result(result)
                .await
                .with_context(|| oof![s ~ "db send"])?
                .recv()
                .await
                .ok()
                .with_context(|| oof![s ~ "db recv"])?
                .with_context(|| oof![s ~ "new_publish"])?;

            Ok((pk, included))
        }

        async fn _publish_from_container(
            &self,
            db::NewPublish { pk, fragments }: db::NewPublish,
            podman: &podman::BorrowedClient<'_>,
            container_id: &str,
        ) -> anyhow::Result<(String, i64)> {
            for db::NewPublishFragment { fragment, build } in fragments {
                let stuff = zstd::decode_all(&fragment[..])
                    .with_context(|| oof![s ~ "zstd decode", build ~ build])?;

                podman
                    .put(format!(
                        "http://p/v6.0.0/libpod/\
                            containers/{container_id}/archive?\
                            path=/publish/web/assets/bundles/"
                    ))
                    .header(CONTENT_TYPE, APPLICATION_XTAR)
                    .body(stuff)
                    .send()
                    .await
                    .expect_status(StatusCode::OK)
                    .with_context(
                        || oof![s ~ "libpod/containers/archive", build ~ build],
                    )?;
            }

            let attach = podman
                .post(format!(
                    "http://p/v6.0.0/libpod/\
                        containers/{container_id}/attach?\
                        stdout=1&stderr=1"
                ))
                .send()
                .await
                .expect_status(StatusCode::OK);

            if let Err(err) = &attach {
                warn!("failed libpod/containers/attach";
                      "container" => &container_id,
                      "err" => logging::dbg(err));
            }

            let start = podman
                .post(format!(
                    "http://p/v6.0.0/libpod/\
                        containers/{container_id}/start"
                ))
                .send()
                .await
                .expect_status(StatusCode::NO_CONTENT)
                .with_context(|| oof![s ~ "libpod/containers/start"])?;

            let mut output: Option<String> = None;

            if let Ok(response) = attach {
                match response.text().await {
                    Ok(s) => output = Some(s),
                    Err(err) => warn!("failed to read attached";
                                      "container" => &container_id,
                                      "err" => logging::err(err)),
                }
            }

            let wait_response = podman
                .post(format!(
                    "http://p/v6.0.0/libpod/containers/\
                        {container_id}/wait"
                ))
                .send()
                .await
                .expect_status(StatusCode::OK)
                .into_text()
                .await
                .with_context(|| oof![s ~ "libpod/containers/wait"])?;

            let exit_code = wait_response
                .trim()
                .parse::<i64>()
                .map_err(|err| {
                    warn!("failed to parse exit status";
                          "s" => "libpod/containers/wait",
                          "err" => logging::err(err),
                          "text" => &wait_response,
                          "pk" => pk,
                          "container" => container_id);
                })
                .unwrap_or(-1);

            Ok((output.unwrap_or_default(), exit_code))
        }
    }
}

pub(crate) mod types {
    use std::fmt;
    use std::str::FromStr;

    use base64::{engine::general_purpose::URL_SAFE_NO_PAD as BASE64, Engine};

    use serde::de::{Deserialize, Deserializer};

    #[derive(Debug)]
    pub struct WorkshopIdOrUrl(pub WorkshopId);

    pub(crate) fn is_workshopid_char(c: char) -> bool {
        c.is_ascii_digit()
    }

    pub(crate) fn is_workshopid_byte(c: u8) -> bool {
        c.is_ascii_digit()
    }

    impl std::ops::Deref for WorkshopIdOrUrl {
        type Target = WorkshopId;

        fn deref(&self) -> &WorkshopId {
            &self.0
        }
    }

    impl<'de> serde::Deserialize<'de> for WorkshopIdOrUrl {
        fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
        where
            D: serde::Deserializer<'de>,
        {
            let s = std::borrow::Cow::<str>::deserialize(deserializer)?;

            crate::parsing::workshopid_from_url(&s)
                .or_else(|| s.parse().ok())
                .map(WorkshopIdOrUrl)
                .ok_or_else(|| {
                    serde::de::Error::custom(format!(
                        "invalid workshop id or workshop item url: {}",
                        &s
                    ))
                })
        }
    }

    #[derive(Debug, Clone, Eq, PartialEq, serde::Serialize)]
    pub struct WorkshopId(String);

    #[derive(Debug, thiserror::Error)]
    #[error("invalid workshop id: {}", .0)]
    pub struct InvalidWorkshopId(String);

    impl FromStr for WorkshopId {
        type Err = InvalidWorkshopId;

        fn from_str(s: &str) -> Result<Self, Self::Err> {
            WorkshopId::try_from(s.to_string())
        }
    }

    impl TryFrom<String> for WorkshopId {
        type Error = InvalidWorkshopId;

        fn try_from(s: String) -> Result<Self, Self::Error> {
            if !s.as_bytes().iter().cloned().all(is_workshopid_byte) {
                return Err(InvalidWorkshopId(s));
            }

            if s.is_empty() {
                return Err(InvalidWorkshopId(s));
            }

            return Ok(WorkshopId(s));
        }
    }

    impl fmt::Display for WorkshopId {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            self.0.fmt(f)
        }
    }

    impl std::ops::Deref for WorkshopId {
        type Target = String;

        fn deref(&self) -> &String {
            &self.0
        }
    }

    pub(crate) mod b64zstd {
        use base64::Engine;
        use serde::{Deserialize, Serialize};

        pub fn serialize<S>(t: &Vec<u8>, er: S) -> Result<S::Ok, S::Error>
        where
            S: serde::Serializer,
        {
            // super::BASE64.encode(t.as_slice()).serialize(er)
            "".serialize(er)
        }

        pub fn deserialize<'de, D>(deserializer: D) -> Result<Vec<u8>, D::Error>
        where
            D: serde::Deserializer<'de>,
        {
            let s = String::deserialize(deserializer)?;
            let b = super::BASE64
                .decode(s.as_bytes())
                .map_err(serde::de::Error::custom)?;
            let b = zstd::decode_all(&b[..]).map_err(serde::de::Error::custom)?;
            Ok(b)
        }
    }

    pub struct ETag(Vec<u8>);

    impl ETag {
        pub fn from_base64<V>(v: V) -> Result<Self, base64::DecodeError>
        where
            V: AsRef<[u8]>,
        {
            BASE64.decode(v.as_ref()).map(Self)
        }

        pub fn to_base64(&self) -> String {
            BASE64.encode(self.0.as_slice())
        }
    }

    impl fmt::Display for ETag {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(f, "{}", self.to_base64())
        }
    }

    impl fmt::Debug for ETag {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.debug_tuple("ETag").field(&self.to_base64()).finish()
        }
    }

    impl std::ops::Deref for ETag {
        type Target = [u8];

        fn deref(&self) -> &[u8] {
            self.0.as_slice()
        }
    }
}

pub mod www {
    use std::borrow::Cow;
    use std::collections::BTreeMap;
    use std::net::IpAddr;
    use std::sync::Arc;
    use std::time::Duration;

    use axum::{
        extract::{Extension, Form, Path, Query},
        http::{header, Request, StatusCode},
        response::{Html, IntoResponse, Response},
        Router,
    };

    use anyhow::Context;

    use base64::{engine::general_purpose::URL_SAFE_NO_PAD as BASE64, Engine};

    // use reqwest::header::{HeaderValue, CONTENT_TYPE};
    use axum::http::header::{HeaderMap, HeaderName, HeaderValue, CONTENT_TYPE};

    use serde_json::json;

    use kanal::{AsyncReceiver as Receiver, AsyncSender as Sender};

    use tokio::sync::{Mutex, Notify};
    use tokio::task::JoinSet;

    use stupid_rate_limit::Rated;

    use crate::db;
    use crate::httpreq;
    use crate::misc::{self, traits::*};
    use crate::parsing::{
        ContentPackage, WorkshopCollectionPage, WorkshopItemChangelog,
        WorkshopItemFileDetails,
    };
    use crate::podman;
    use crate::publish;
    use crate::steamcmd;
    use crate::steamweb;
    use crate::types::{ETag, InvalidWorkshopId, WorkshopId, WorkshopIdOrUrl};
    use crate::{
        aux, butt, crit,
        errslop::{traits::*, Oof},
        impl_from_err, logging, oof, warn, Config,
    };

    pub const APPLICATION_XTAR: HeaderValue =
        HeaderValue::from_static("application/x-tar");
    pub const APPLICATION_JSON: HeaderValue =
        HeaderValue::from_static("application/json");

    const FOUR_KILOBYTES: usize = 2usize.saturating_pow(12);

    pub struct Server {
        pub listener: tokio::net::TcpListener,
        pub sys_r: Receiver<super::SystemMsg>,
        pub db: db::Client,
        pub cfg: Arc<Config>,
        pub podman: podman::Client,
        pub steamcmd: steamcmd::Client,
        pub steamweb: steamweb::Client,
        pub publish: Sender<publish::Msg>,
    }

    fn path_for_workshop_item(pk: i64) -> String {
        format!("/workshop-item/{pk}/")
    }

    fn path_for_build(pk: i64) -> String {
        format!("/build/{pk}/")
    }

    // fn path_for_refresh(workshopid: &WorkshopId) -> String {
    //     format!("/workshop-item/refresh/{workshopid}/")
    // }

    impl Server {
        pub async fn run_forever(self) -> std::io::Result<()> {
            use axum::routing::{get, post, Router};
            use axum::ServiceExt;

            let tasks = BackgroundTasks::new();

            /* ROUTES */

            let app = Router::new()
                .route("/ping/", get(ping))
                .route("/workshop-item/", get(list))
                .route("/workshop-item/", post(refresh_workshop_item))
                // .route("/workshop-item/refresh/{id}/", post(refresh_workshop_item))
                .route("/workshop-item/{pk}/", get(workshop_item_by_pk))
                .route(
                    "/workshop-item/{pk}/download/",
                    post(download_workshop_item),
                )
                .route("/build/", post(submit_build))
                .route("/build/{pk}/", get(get_build))
                // .route("/build/{pk}/wait/", get(wait_on_build))
                .route("/publish/{pk}/wait/", get(wait_on_publish))
                // .route("/build/{pk}/publish/", get(build_fragments))
                /* todo guard behind auth */
                .route("/x/workshop-item/{pk}/file/", get(get_workshop_item_file))
                .route("/x/build/{pk}/fragment/", get(get_build_fragment))
                .route("/x/republish/", post(republish))
                .route("/x/rate-limits/", get(dump_rate_limits))
                .layer(Extension(self.podman))
                .layer(Extension(self.steamcmd))
                .layer(Extension(self.steamweb))
                .layer(Extension(self.publish))
                .layer(Extension(self.db.clone()))
                .layer(Extension(tasks.clone()))
                // .layer(Extension(FragmentBuildTasks::new()))
                .layer(Extension(RateLimit::new()))
                .layer(axum::middleware::from_fn(config_headers))
                .layer(Extension(Arc::clone(&self.cfg)))
                .layer(axum::middleware::from_fn(log_stuff));

            let mut serve = tokio::task::spawn(async move {
                axum::serve(self.listener, app)
                    .with_graceful_shutdown(
                        async move { drop(self.sys_r.recv().await) },
                    )
                    .await
            });

            let BackgroundTasks(tasks, tasks_cleanup) = tasks;
            loop {
                tokio::select! {
                    biased;

                    res = &mut serve => {
                        match res {
                            Ok(Ok(())) => (),
                            Ok(Err(err)) => crit!("serve failed"; "err" => logging::err(err)),
                            Err(joinerr) => crit!("serve panic?"; "err" => logging::err(joinerr)),
                        };
                        break;
                    }

                    _ = tasks_cleanup.notified() => {
                        while let Some(res) = tasks.lock().await.try_join_next() {
                            match res {
                                Ok(()) => (),
                                Err(joinerr) => crit!("www joinset"; "err" => logging::err(joinerr)),
                            }
                        }
                    }
                }
            }

            let mut joinset = tasks.lock().await;

            while let Some(res) = joinset.join_next().await {
                match res {
                    Ok(()) => (),
                    Err(joinerr) => {
                        crit!("www joinset"; "err" => logging::err(joinerr))
                    }
                }
            }

            Ok(())
        }
    }

    async fn config_headers(
        Extension(cfg): Extension<Arc<Config>>,
        request: axum::extract::Request,
        next: axum::middleware::Next,
    ) -> Response {
        let mut response = next.run(request).await;
        response
            .headers_mut()
            .extend(cfg.response_headers.0.iter().cloned());
        response
    }

    async fn log_stuff(
        request: axum::extract::Request,
        next: axum::middleware::Next,
    ) -> Response {
        let req = format!("{} {}", request.method(), request.uri());
        let start = std::time::Instant::now();

        let response = next.run(request).await;

        let duration = std::time::Instant::now().saturating_duration_since(start);
        butt!(""; "req" => format!("{} {} {}", req, response.status(), logging::time(duration)));
        response
    }

    #[derive(Debug, Clone)]
    struct BackgroundTasks(Arc<Mutex<JoinSet<()>>>, Arc<Notify>);

    impl BackgroundTasks {
        fn new() -> Self {
            Self(
                Arc::new(Mutex::new(JoinSet::new())),
                Arc::new(Notify::new()),
            )
        }

        fn notif(&self) -> Arc<Notify> {
            self.1.clone()
        }

        /* this is used to continue handling a request without being cancelled
         * when the requester diconnects */
        async fn spawn<T, F>(&self, f: F) -> Receiver<T>
        where
            F: Future<Output = T> + Send + 'static,
            T: Send + 'static,
        {
            let (s, r) = kanal::bounded_async(1);
            let notif = self.notif();
            self.0.lock().await.spawn(async move {
                let _ = s.send(f.await).await;
                notif.notify_one()
            });
            r
        }
    }

    // #[derive(Debug, Clone)]
    // struct FragmentBuildTasks(Arc<Mutex<BTreeMap<i64, Receiver<()>>>>);

    // impl std::ops::Deref for FragmentBuildTasks {
    //     type Target = Mutex<BTreeMap<i64, Receiver<()>>>;

    //     fn deref(&self) -> &Self::Target {
    //         &self.0
    //     }
    // }

    // impl FragmentBuildTasks {
    //     fn new() -> Self {
    //         Self(Arc::new(Mutex::new(BTreeMap::new())))
    //     }
    // }

    #[derive(Debug, Clone)]
    struct RateLimit(Arc<Mutex<Rated<IpAddr>>>);

    impl RateLimit {
        fn new() -> Self {
            Self(Arc::new(Mutex::new(
                stupid_rate_limit::Options {
                    interval: Duration::from_secs(30_000),
                    length: 40, /* 20 minutes */
                    capacity: 400,
                }
                .build(),
            )))
        }
    }

    impl std::ops::Deref for RateLimit {
        type Target = Mutex<Rated<IpAddr>>;

        fn deref(&self) -> &Self::Target {
            &self.0
        }
    }

    // struct RateLimited<const N: stupid_rate_limit::Ticket>;
    #[derive(Debug, Clone)]
    struct RateLimited(RateLimit, Option<IpAddr>);

    impl RateLimited {
        async fn limit(
            &self,
            v: stupid_rate_limit::Ticket,
        ) -> Result<(), stupid_rate_limit::OverLimit> {
            let RateLimited(mutex, ip) = self;
            if let Some(ip) = ip {
                mutex.lock().await.add(*ip, v)
            } else {
                Ok(())
            }
        }

        async fn read(&self) -> Option<stupid_rate_limit::Ticket> {
            let RateLimited(mutex, ip) = self;
            mutex.lock().await.entry_sum(ip.as_ref()?)
        }

        async fn entries(&self) -> Vec<(IpAddr, stupid_rate_limit::Ticket)> {
            let RateLimited(mutex, ip) = self;
            mutex.lock().await.entries()
        }
    }

    use axum::extract::FromRequestParts;
    // use axum::http::header::{HeaderKey, HeaderMap, HeaderValue};

    impl<S> FromRequestParts<S> for RateLimited
    where
        S: Send + Sync,
    {
        type Rejection = Response;

        async fn from_request_parts(
            parts: &mut axum::http::request::Parts,
            state: &S,
        ) -> Result<Self, Self::Rejection> {
            use axum::RequestPartsExt;

            let Extension(rate) = parts
                .extract::<Extension<RateLimit>>()
                .await
                .map_err(|err| err.into_response())?;

            let headers = parts
                .extract::<HeaderMap>()
                .await
                .map_err(|err| err.into_response())?;

            Ok(RateLimited(rate, remote_ip(&headers)))
        }
    }

    fn remote_ip(hs: &HeaderMap) -> Option<IpAddr> {
        for header in [
            HeaderName::from_static("cf-connecting-ip"),
            HeaderName::from_static("x-forwarded-for"),
        ]
        .iter()
        {
            let Some(v) = hs.get(header) else {
                continue;
            };

            let Some(ip) = v.to_str().ok().and_then(|s| s.parse().ok()) else {
                warn!("header found but not IpAddr";
                      "header" => header,
                      "value" => logging::dbg(v));
                continue;
            };

            return Some(ip);
        }

        None
    }

    // const COST_BUILD: stupid_rate_limit::Ticket = 1;
    const COST_DOWNLOAD: stupid_rate_limit::Ticket = 1;
    const COST_WORKSHOP: stupid_rate_limit::Ticket = 1;

    /* request handlers? */

    async fn ping(rate: RateLimited) -> Response {
        match rate.read().await {
            Some(v) => v.to_string().into_response(),
            None => "pong".into_response(),
        }
    }

    async fn get_build(
        Path(pk): Path<i64>,
        Extension(db): Extension<db::Client>,
    ) -> Result<Response, Response> {
        db.get_build(pk)
            .recv_or_busy()
            .await?
            .map_err(internal_error)?
            .ok_or_else(|| not_found_response())
            .and_then(json_response)
    }

    /* todo avoid duplicate builds? */
    async fn submit_build(
        // rate: RateLimited,
        Extension(cfg): Extension<Arc<Config>>,
        Extension(bg): Extension<BackgroundTasks>,
        Extension(bg_for_cleanup): Extension<BackgroundTasks>,
        Extension(db): Extension<db::Client>,
        Extension(podman): Extension<podman::Client>,
        Extension(podman_for_cleanup): Extension<podman::Client>,
        Extension(publish): Extension<Sender<publish::Msg>>,
        // Extension(build_tasks): Extension<FragmentBuildTasks>,
        body: axum::body::Body,
    ) -> Result<Response, Response> {
        use crate::podman::traits::*;
        use std::iter::once;

        let req: db::SaveBuild = parse_json_with_limit(body, FOUR_KILOBYTES).await?;

        // rate.limit(COST_BUILD).await.map_err(rate_limited)?;

        butt!("submit build"; "req" => logging::dbg(&req));

        bg.spawn(async move {
            let res = db
                .save_build(req)
                .recv_or_busy()
                .await?
                .map_err(internal_error)?;

            let pk = match res {
                db::SaveBuildResult::Inserted { pk } => pk,
                db::SaveBuildResult::Missing { missing } => {
                    let value = json!({ "missing": missing });
                    let body = serde_json::to_string(&value)
                        .with_context(|| oof![json ~ logging::dbg(value)])
                        .map_err(internal_error);
                    return Err((StatusCode::BAD_REQUEST, body).into_response())?;
                }
            };

            let db::BuildItemFiles { files } = db
                .build_item_files(pk)
                .recv_or_busy()
                .await?
                .map_err(internal_error)?;

            let podman = podman
                .acquire()
                .await
                .with_context(|| oof![s ~ "acquire podman"])
                .map_err(internal_error)?;

            let content_paths =
                files.iter().map(|(i, _)| json!(format!("/baro/mod/{i}")));
            let package_names = files.iter().map(|(i, _)| json!(format!("{i}")));
            let command = [
                json!("--no-index"),
                json!("--output"),
                json!("/baro/fragments/"),
                json!("--content"),
                json!("/baro/vanilla/"),
            ]
            .into_iter()
            .chain(content_paths)
            .chain(once(json!("--named-load-order")))
            .chain(once(json!(format!("{pk}"))))
            .chain(package_names)
            .collect::<Vec<_>>();

            let create = json!({
                "name": format!("materialist-build-{}", pk),
                "image": "baro-data",
                "terminal": true,
                "command": command,
                "volumes": [
                    {"name": "barotrauma", "dest": "/baro/vanilla"},
                ],
            });

            let container_id = podman
                .post("http://p/v6.0.0/libpod/containers/create")
                .json(&create)
                .expect_status(StatusCode::CREATED)
                .await
                .parse_json::<podman::CreateResponse>()
                .with_context(|| {
                    oof![s ~ "libpod/containers/create",
                         req ~ create.clone()]
                })
                .map(|podman::CreateResponse { Id, Warnings }| {
                    if !Warnings.is_empty() {
                        warn!("libpod/containers/create";
                              "warnings" => logging::dbg(Warnings),
                              "req" => &create);
                    }
                    Arc::new(Id)
                })
                .map_err(internal_error)?;

            butt!("libpod/containers/create";
                  "req" => &create, "id" => &container_id);

            /* ready up a delete job for the container */

            let drop_container_id = Arc::clone(&container_id);
            let (_drop_to_remove, remove) = kanal::bounded_async::<()>(0);

            bg_for_cleanup
                .spawn(async move {
                    let _ = remove.recv().await;

                    let Some(podman) = podman_for_cleanup.acquire().await else {
                        return warn!("build container cleanup failed to acquire podman";
                                     "container" => &drop_container_id);
                    };

                    let res = podman
                        .delete(format!(
                            "http://p/v6.0.0/libpod/\
                                containers/{drop_container_id}?force=1"
                        ))
                        .send_and_read_json()
                        .await
                        .expect_status(StatusCode::OK);

                    if let Err(err) = res {
                        warn!("failed libpod/containers/delete";
                              "container" => &drop_container_id,
                              "err" => logging::err(err));
                    }
                })
                .await;

            let attach = podman
                .post(format!(
                    "http://p/v6.0.0/libpod/\
                        containers/{container_id}/attach?stdout=1&stderr=1"
                ))
                .send()
                .await
                .expect_status(StatusCode::OK);

            if let Err(err) = &attach {
                warn!("failed libpod/containers/attach";
                      "container" => &container_id, "err" => logging::dbg(err));
            }

            for (pk, data) in files {
                // let Some(data) = data else { continue };

                let stuff = zstd::decode_all(&data[..])
                    .with_context(|| oof![s ~ "zstd decode", pk ~ pk])
                    .map_err(internal_error)?;

                podman
                    .put(format!(
                        "http://p/v6.0.0/libpod/\
                            containers/{container_id}/archive?path=/baro/mod/{pk}"
                    ))
                    .header(CONTENT_TYPE, APPLICATION_XTAR)
                    .body(stuff)
                    .send()
                    .await
                    .expect_status(StatusCode::OK)
                    .with_context(|| {
                        oof![s ~ "libpod/containers/archive",
                             pk ~ pk,
                             container ~ container_id.clone()]
                    })
                    .map_err(internal_error)?;
            }

            let start = podman
                .post(format!(
                    "http://p/v6.0.0/libpod/containers/{container_id}/start"
                ))
                .send()
                .await
                .expect_status(StatusCode::NO_CONTENT)
                .with_context(|| {
                    oof![s ~ "libpod/containers/start",
                         container ~ container_id.clone()]
                })
                .map_err(internal_error)?;

            let mut output: Option<String> = None;

            if let Ok(response) = attach {
                match response.text().await {
                    Ok(s) => output = Some(s),
                    Err(err) => warn!("failed to read attached";
                                      "container" => &container_id,
                                      "err" => logging::err(err)),
                }
            }

            let wait_response = podman
                .post(format!(
                    "http://p/v6.0.0/libpod/containers/{container_id}/wait"
                ))
                .send()
                .await
                .expect_status(StatusCode::OK)
                .into_text()
                .await
                .with_context(|| {
                    oof![s ~ "libpod/containers/wait",
                         container ~ container_id.clone()]
                })
                .map_err(internal_error)?;

            let exit_code = wait_response
                .trim()
                .parse::<i64>()
                .map_err(|err| {
                    warn!("failed to parse exit status";
                          "s" => "libpod/containers/wait",
                          "err" => logging::err(err),
                          "text" => &wait_response,
                          "container" => container_id);
                })
                .unwrap_or(-1);

            let fragment = podman
                .get_files(&container_id, "/baro/fragments/.")
                .await
                .and_then(|fragment_tar| {
                    zstd::encode_all(&fragment_tar[..], 0).with_context(|| {
                        oof![s ~ "zstd fragment",
                             container ~ container_id.clone()]
                    })
                })
                .map_err(|err| {
                    warn!("failed to read build fragment";
                          "err" => logging::err(err),
                          "container" => container_id);
                })
                .ok()
                .unwrap_or_default();

            db.save_build_result(db::BuildResult {
                pk,
                exit_code,
                output: output.unwrap_or_default(),
                fragment,
            })
            .recv_or_busy()
            .await?
            .map_err(internal_error)?;

            /* TODO publish request */
            {
                let (reply, _) = kanal::bounded_async(1);
                if let Err(err) = publish.send(publish::Msg::Publish { reply }).await {
                    warn!("failed to submit publish request for build"; "pk" => pk);
                }
            }

            Ok(see_other(path_for_build(pk)))
        })
        .await
        .recv()
        .await
        .with_context(|| oof![s ~ "build task recv"])
        .map_err(internal_error)?
    }

    // async fn wait_on_build(
    //     Path(pk): Path<i64>,
    //     Extension(db): Extension<db::Client>,
    //     Extension(build_tasks): Extension<FragmentBuildTasks>,
    // ) -> Result<Response, Response> {
    //     if let Some(r) = build_tasks.lock().await.get(&pk).cloned() {
    //         r.recv().await.map_err(internal_error)?;
    //     }

    //     return Ok(see_other(path_for_build(pk)));
    // }

    async fn wait_on_publish(
        Path(pk): Path<i64>,
        Extension(cfg): Extension<Arc<Config>>,
        Extension(db): Extension<db::Client>,
    ) -> Result<Response, Response> {
        let (exit_code, url) = loop {
            let db::Publish { pk: _, exit_code, public_url } = db
                .get_publish(pk)
                .recv_or_busy()
                .await?
                .map_err(internal_error)?
                .ok_or_else(|| not_found_response())?;

            if let Some(exit_code) = exit_code {
                break (exit_code, public_url);
            }

            tokio::time::sleep(cfg.wait_on_publish_poll_interval).await;
        };

        // return Ok(StatusCode::NO_CONTENT.into_response());
        return json_response(json!({
            "exit_code": exit_code,
            "public_url": url,
        }));
    }

    async fn republish(
        Extension(publish): Extension<Sender<publish::Msg>>,
    ) -> Result<Response, Response> {
        let (reply, reply_r) = kanal::bounded_async(1);
        publish
            .send(publish::Msg::Publish { reply })
            .await
            .map_err(too_busy)?;
        reply_r
            .recv()
            .await
            .map(|r| r.pk)
            .map_err(too_busy)
            .and_then(json_response)
    }

    async fn dump_rate_limits(rate: RateLimited) -> Result<Response, Response> {
        json_response(rate.entries().await)
    }

    #[derive(serde::Deserialize)]
    struct ListRequest {
        workshopid: WorkshopIdOrUrl,
    }

    async fn list(
        Extension(db): Extension<db::Client>,
        Query(req): Query<ListRequest>,
    ) -> Result<Response, Response> {
        let ListRequest { workshopid: WorkshopIdOrUrl(workshopid) } = req;

        let items: Vec<db::WorkshopItem> = db
            .workshop_items(workshopid)
            .recv_or_busy()
            .await?
            .map_err(internal_error)?;

        if items.is_empty() {
            return Err(not_found_response());
        }

        json_response(items)
    }

    async fn workshop_item_by_pk(
        Path(pk): Path<i64>,
        Extension(db): Extension<db::Client>,
    ) -> Result<Response, Response> {
        db.workshop_item_by_pk(pk)
            .recv_or_busy()
            .await?
            .map_err(internal_error)?
            .ok_or_else(|| not_found_response())
            .and_then(json_response)
    }

    async fn get_workshop_item_file(
        Path(pk): Path<i64>,
        Extension(db): Extension<db::Client>,
    ) -> Result<Response, Response> {
        let b: bytes::Bytes = db
            .workshop_item_file(pk)
            .recv_or_busy()
            .await?
            .map_err(internal_error)?
            .ok_or_else(|| not_found_response())?;
        Ok((
            StatusCode::OK,
            [(header::CONTENT_TYPE, APPLICATION_XTAR)],
            b,
        )
            .into_response())
    }

    async fn get_build_fragment(
        Path(pk): Path<i64>,
        Extension(db): Extension<db::Client>,
    ) -> Result<Response, Response> {
        let b: bytes::Bytes = db
            .build_fragment(pk)
            .recv_or_busy()
            .await?
            .map_err(internal_error)?
            .ok_or_else(|| not_found_response())?;
        Ok((
            StatusCode::OK,
            [(header::CONTENT_TYPE, APPLICATION_XTAR)],
            b,
        )
            .into_response())
    }

    #[derive(serde::Deserialize)]
    struct RefreshRequest {
        workshopid: WorkshopIdOrUrl,
    }

    #[derive(Debug, serde::Serialize)]
    struct CollectionResponse {
        pub workshopid: WorkshopId,
        pub title: String,
        pub authors: Vec<String>,
        pub collection: Box<[WorkshopId]>,
    }

    /* This will accept a workshop id or workshop item url.
     * The item can be of a collection or a normal workshop file.
     *
     * In the case of a normal workshop file,
     * this function will add the file to the database and return
     * 303 See Other with a location of where to find the workshop
     * item details.
     *
     * In the case of a collection, this will return 200 Ok with
     * an object containing the collection details and an "items"
     * property listing workshop ids for each workshop item.
     *
     * (TODO consider returning workshop URLs instead of ids?) */
    async fn refresh_workshop_item(
        rate: RateLimited,
        Extension(cfg): Extension<Arc<Config>>,
        Extension(steamweb): Extension<steamweb::Client>,
        Extension(db): Extension<db::Client>,
        Extension(bg): Extension<BackgroundTasks>,
        body: axum::body::Body,
    ) -> Result<Response, Response> {
        // let workshopid = WorkshopId::try_from(workshopid).map_err(bad_request)?;

        let RefreshRequest { workshopid: WorkshopIdOrUrl(workshopid) } =
            parse_json_with_limit(body, FOUR_KILOBYTES).await?;

        rate.limit(COST_WORKSHOP).await.map_err(rate_limited)?;

        bg.spawn(async move {
            let steamweb = steamweb
                .acquire()
                .await
                .with_context(|| oof![s ~ "acquire steamweb"])
                .map_err(internal_error)?;

            /* this should work on both items and collections */
            let details: WorkshopItemFileDetails = steamweb
                .filedetails(&cfg.steamcommunity_url, &workshopid)
                .await
                .map_err(not_found)?;

            /* the workshop id on the page can be different from the URL, like ...
             *   /filedetails/?id=1234abc
             * ... will serve the same content as ...
             *   /filedetails/?id=1234
             * In either case, we'll use the workshopid from the page
             * and it should be fine */
            drop(workshopid); /* don't use this on accident */

            if details.appid != crate::BARO_APPID {
                return Err((StatusCode::NOT_FOUND, "workshop item has wrong appid")
                    .into_response())?;
            }

            if let Ok(log) = steamweb
                .changelog(&cfg.steamcommunity_url, &details.workshopid)
                .await
            {
                drop(steamweb);

                let (_did_insert, pk) = db
                    .upsert_workshop_item(db::NewWorkshopItem {
                        workshopid: details.workshopid,
                        title: details.title,
                        authors: details.authors,
                        version: log.latest_timestamp,
                    })
                    .recv_or_busy()
                    .await?
                    .map_err(internal_error)?;

                /* created doesn't cause a redirect for the client,
                 * since we don't return the full object here,
                 * always send see_other */
                return Ok(see_other(path_for_workshop_item(pk)));
            }

            /* FIXME we already made a filedetails request, don't make a second one here */

            if let Ok(collection) = steamweb
                .collection(&cfg.steamcommunity_url, &details.workshopid)
                .await
            {
                drop(steamweb);

                let value = CollectionResponse {
                    workshopid: details.workshopid,
                    title: details.title,
                    authors: details.authors,
                    collection: collection.items,
                };

                return serde_json::to_string(&value)
                    .map(|s| s.into_response())
                    .with_context(|| oof![json ~ logging::dbg(value)])
                    .map_err(internal_error);
            }

            warn!("found details but no changelog or collection";
                  "details" => logging::dbg(&details));

            return Err(not_found_response());
        })
        .await
        .recv()
        .await
        .with_context(|| oof![s ~ "refresh_workshop_item task recv"])
        .map_err(internal_error)?
    }

    /* Request a download for a specific version of a workshop item.
     *
     * If the file is already downloaded, then return 303 See Other.
     *
     * If we see a more recent version of the item on steam, then
     * return 410 Gone. We can only download the most recent version. */
    async fn download_workshop_item(
        rate: RateLimited,
        Path(pk): Path<i64>,
        Extension(cfg): Extension<Arc<Config>>,
        Extension(steamweb): Extension<steamweb::Client>,
        Extension(steamcmd): Extension<steamcmd::Client>,
        Extension(db): Extension<db::Client>,
        Extension(bg): Extension<BackgroundTasks>,
    ) -> Result<Response, Response> {
        let item: db::WorkshopItem = db
            .workshop_item_by_pk(pk)
            .recv_or_busy()
            .await?
            .map_err(internal_error)?
            .ok_or_else(|| not_found_response())?;

        if item.file.is_some() {
            return Ok(see_other(path_for_workshop_item(pk)));
        }

        rate.limit(COST_DOWNLOAD).await.map_err(rate_limited)?;

        let latest: i64 = steamweb
            .acquire()
            .await
            .with_context(|| oof![s ~ "acquire steamweb"])
            .map_err(internal_error)?
            .changelog(&cfg.steamcommunity_url, &item.workshopid)
            .await
            .with_context(|| oof![s ~ "steamweb changelog"])
            .map_err(internal_error)?
            .latest_timestamp;

        if item.version != latest {
            return Ok(gone());
        }

        /* runs in a task to prevent cancellation */
        bg.spawn(async move {
            let file: steamcmd::DownloadedFile = steamcmd
                .acquire()
                .await
                .with_context(|| oof![s ~ "acquire steamcmd"])
                .map_err(internal_error)?
                .download(&cfg.steamcmd_url, &item.workshopid)
                .await
                .with_context(|| oof![s ~ "steamcmd download"])
                .map_err(internal_error)?;

            let new_latest: i64 = steamweb
                .acquire()
                .await
                .with_context(|| oof![s ~ "acquire steamweb"])
                .map_err(internal_error)?
                .changelog(&cfg.steamcommunity_url, &item.workshopid)
                .await
                .with_context(|| oof![s ~ "steamweb changelog"])
                .map_err(internal_error)?
                .latest_timestamp;

            if latest != new_latest {
                warn!("latest workshop item changed while downloading?";
                      "latest" => latest, "new_latest" => new_latest);
                return Ok(gone());
            }

            let data: bytes::Bytes = file.data.clone();

            let file_pk = db
                .save_file(item.pk, file)
                .send_and_recv()
                .await
                .with_context(|| oof![s ~ "db::Client"])
                .map_err(too_busy)?
                .map_err(internal_error)?;

            if let Ok(p) = extract_filelist(data)
                .await
                .map_err(|err| {
                    warn!("failed to extract filelist";
                          "item.pk" => item.pk,
                          "err" => logging::err(err))
                })
                .and_then(|filelist| {
                    ContentPackage::from_filelist(&filelist).ok_or_else(|| {
                        warn!("no content package found";
                              "item.pk" => item.pk,
                              "filelist" => &filelist)
                    })
                })
            {
                if let Err(err) = db
                    .save_content_package(file_pk, p.clone())
                    .send_and_recv()
                    .await
                    .with_context(|| oof![s ~ "db::Client"])
                    .map_err(too_busy)?
                {
                    warn!("failed to save content package";
                          "item.pk" => item.pk,
                          "contentpackage" => logging::dbg(p))
                }
            }

            Ok(see_other(path_for_workshop_item(pk)))
        })
        .await
        .recv()
        .await
        .with_context(|| oof![s ~ "download_workshop_item task recv"])
        .map_err(internal_error)?
    }

    /* error formatting */

    pub fn not_found<E>(err: E) -> Response
    where
        anyhow::Error: From<E>,
    {
        crit!("not found"; "err" => crate::logging::err(err));
        (StatusCode::NOT_FOUND, "not found").into_response()
    }

    pub fn not_found_response() -> Response {
        (StatusCode::NOT_FOUND, "not found").into_response()
    }

    pub fn internal_error<E>(err: E) -> Response
    where
        anyhow::Error: From<E>,
    {
        crit!("internal error"; "err" => crate::logging::err(err));
        internal_error_response()
    }

    pub fn internal_error_response() -> Response {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            "something went wrong with the computer and your thing didn't work, sorry =C",
        )
            .into_response()
    }

    pub fn too_busy<E>(err: E) -> Response
    where
        anyhow::Error: From<E>,
    {
        crit!("too busy"; "err" => crate::logging::err(err));
        (
            StatusCode::SERVICE_UNAVAILABLE,
            "this computer too busy (x . x) ~~zzZ",
        )
            .into_response()
    }

    pub fn rate_limited(err: stupid_rate_limit::OverLimit) -> Response {
        (
            StatusCode::TOO_MANY_REQUESTS,
            "you are being rate limited (((＞.＜)))",
        )
            .into_response()
    }

    pub fn bad_request(err: BadRequest) -> Response {
        /* TODO, this formats the response body weird,
         * should be formatted without context */
        let body = format!("{}", &err);
        // crit!("bad request"; "err" => &err);
        (StatusCode::BAD_REQUEST, body).into_response()
    }

    pub fn redirect<S: AsRef<str>>(s: S) -> Response {
        (
            StatusCode::PERMANENT_REDIRECT,
            [(header::LOCATION, s.as_ref())],
        )
            .into_response()
    }

    pub fn created<S: AsRef<str>>(s: S) -> Response {
        (StatusCode::CREATED, [(header::LOCATION, s.as_ref())]).into_response()
    }

    /* according to MDN, See Other changes the request method to GET?
     * xh at least doesn't respect that though, probably because everything rust is dogshit  */
    pub fn see_other<S: AsRef<str>>(s: S) -> Response {
        (StatusCode::SEE_OTHER, [(header::LOCATION, s.as_ref())]).into_response()
    }

    // pub fn gone<S: AsRef<str>>(s: S) -> Response {
    //     (StatusCode::GONE, [(header::LOCATION, s.as_ref())]).into_response()
    // }

    pub fn gone() -> Response {
        StatusCode::GONE.into_response()
    }

    /* shortcuts */

    #[derive(Debug, thiserror::Error)]
    pub enum BadRequest {
        // #[error("invalid workshop id: {}", .0)]
        // WorkshopId(String),
        // #[error("expected `workshopid` in request body")]
        // NoWorkshopId,
        #[error("{}", .0)]
        Json(#[from] serde_json::Error),
    }

    use traits::*;

    pub mod traits {
        use axum::response::Response;
        use kanal::AsyncReceiver as Receiver;

        pub trait SendAndRecv<T: Send> {
            fn send_and_recv(self) -> impl Future<Output = Option<T>> + Send;
        }

        impl<T: Send, F> SendAndRecv<T> for F
        where
            F: Future<Output = Option<Receiver<T>>> + Send,
        {
            fn send_and_recv(self) -> impl Future<Output = Option<T>> {
                use crate::aux;
                return async move {
                    let r = self.await?;
                    let t = r.recv().await.ok()?;
                    Some(t)
                };
            }
        }

        pub trait RecvOrBusy<T: Send> {
            fn recv_or_busy(self) -> impl Future<Output = Result<T, Response>> + Send;
        }

        impl<T: Send, F> RecvOrBusy<T> for F
        where
            F: Future<Output = Option<Receiver<T>>> + Send,
        {
            #[track_caller]
            fn recv_or_busy(self) -> impl Future<Output = Result<T, Response>> + Send
            where
                Self: Sized + Send,
            {
                use crate::oof;
                use anyhow::Context;

                return async move {
                    Ok(self
                        .send_and_recv()
                        .await
                        .with_context(|| oof![s ~ "db::Client"])
                        .map_err(super::too_busy)?)
                };
            }
        }
    }

    async fn extract_filelist(tarzstd: bytes::Bytes) -> anyhow::Result<String> {
        /* TODO filelist may not always be at this path */
        Ok(tokio::process::Command::new("tar")
            .args(["--to-stdout", "--zstd", "-x", "./filelist.xml"])
            .to_completion(&*tarzstd)
            .await
            .and_then(|c| c.into_stdout())?)
    }

    async fn parse_json_with_limit<T>(
        body: axum::body::Body,
        limit: usize,
    ) -> Result<T, Response>
    where
        for<'l> T: serde::Deserialize<'l>,
    {
        let bytes = axum::body::to_bytes(body, limit)
            .await
            .with_context(|| oof![s ~ "read request body"])
            .map_err(internal_error)?;

        let t: T = serde_json::from_slice(&bytes[..])
            .map_err(BadRequest::Json)
            .map_err(bad_request)?;

        Ok(t)
    }

    #[track_caller]
    fn json_response<T>(t: T) -> Result<Response, Response>
    where
        T: serde::Serialize + std::fmt::Debug + Send + Sync + 'static,
    {
        serde_json::to_string(&t)
            .with_context(|| oof![json ~ logging::dbg(t)])
            .map(|body| {
                (
                    StatusCode::OK,
                    [(header::CONTENT_TYPE, APPLICATION_JSON)],
                    body,
                )
                    .into_response()
            })
            .map_err(internal_error)
    }
}

pub mod steamcmd {
    use anyhow::{anyhow, Context};

    use crate::{
        httpreq, logging, oof,
        types::{ETag, WorkshopId},
        BARO_APPID,
    };

    #[derive(Debug, Clone)]
    pub struct Client(httpreq::Client);

    impl Client {
        pub async fn acquire<'l>(&'l self) -> Option<BorrowedClient<'l>> {
            self.0.acquire().await.map(BorrowedClient)
        }
    }

    impl From<httpreq::Client> for Client {
        fn from(o: httpreq::Client) -> Self {
            Self(o)
        }
    }

    #[derive(Debug)]
    pub struct BorrowedClient<'l>(httpreq::BorrowedClient<'l>);

    impl<'l> std::ops::Deref for BorrowedClient<'l> {
        type Target = httpreq::BorrowedClient<'l>;

        fn deref(&self) -> &httpreq::BorrowedClient<'l> {
            &self.0
        }
    }

    impl<'l> BorrowedClient<'l> {
        pub async fn download(
            &self,
            base: &reqwest::Url,
            workshopid: &WorkshopId,
        ) -> anyhow::Result<DownloadedFile> {
            let url = download_url(base, workshopid);
            let response = self.post(&url).header("prefer", "wait=20").send().await?;

            /* TODO handle a busy response */
            if let Err(err) = response.error_for_status_ref() {
                let body = response.text().await;
                return Err(err)
                    .context(oof![url ~ url, resp ~ logging::result(body)])?;
            }

            let headers = response.headers();

            let Some(etag) = headers
                .get("etag")
                .map(|v| trim_surrounding_quotes(v.as_ref()))
                .and_then(|v| ETag::from_base64(v).ok())
            // .map(Arc::new)
            else {
                return Err(anyhow!("missing or invalid etag header"))
                    .context(oof![url ~ url,
                                  etag ~ logging::dbg(headers.get("etag").cloned())])?;
            };

            let Some(size) = headers
                .get("uncompressed-size")
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.parse::<i64>().ok())
            else {
                return Err(anyhow!("missing or invalid uncompressed-size header"))
                    .context(oof![url ~ url,
                                  uncompressed_size ~ logging::dbg(headers.get("uncompressed-size").cloned())])?;
            };

            let data: bytes::Bytes = response
                .bytes()
                .await
                .with_context(|| oof![s ~ "read body"])?;

            return Ok(DownloadedFile { size, etag, data });
        }
    }

    #[derive(Debug)]
    pub struct DownloadedFile {
        pub size: i64,
        pub etag: ETag,
        pub data: bytes::Bytes,
    }

    fn download_url(base: &reqwest::Url, workshopid: &WorkshopId) -> String {
        format!(
            "{base}download/{BARO_APPID}/{workshopid}.tar.zstd\
                ?exclude=*.ogg\
                &exclude=*.sub\
                &exclude=*.dll\
                &exclude=*.so\
                &exclude=*.pdb",
        )
    }

    fn trim_surrounding_quotes(mut s: &[u8]) -> &[u8] {
        if s.get(0).cloned() == Some(b'"') {
            s = s.get(1..).unwrap_or(s);
        }
        if s.last().cloned() == Some(b'"') {
            s = s.get(..s.len() - 1).unwrap_or(s);
        }
        s
    }
}

pub(crate) mod podman {
    use std::io;

    use tokio::process::Command;

    use serde_json::{json, Value as JsonValue};

    use reqwest::{Method, StatusCode};

    use anyhow::Context;

    use crate::errslop::{traits::*, Oof};
    use crate::httpreq;
    use crate::logging;
    use crate::misc::{self, traits::*, CompletedProcess};
    use crate::{aux, impl_from_err, oof};

    #[derive(Debug, Clone)]
    pub struct Client(httpreq::Client);

    impl Client {
        pub async fn acquire<'l>(&'l self) -> Option<BorrowedClient<'l>> {
            self.0.acquire().await.map(BorrowedClient)
        }
    }

    impl From<httpreq::Client> for Client {
        fn from(o: httpreq::Client) -> Self {
            Self(o)
        }
    }

    #[derive(Debug)]
    pub struct BorrowedClient<'l>(httpreq::BorrowedClient<'l>);

    impl<'l> std::ops::Deref for BorrowedClient<'l> {
        type Target = httpreq::BorrowedClient<'l>;

        fn deref(&self) -> &httpreq::BorrowedClient<'l> {
            &self.0
        }
    }

    impl<'l> BorrowedClient<'l> {
        /* unused? */
        pub async fn req_and_read(
            &self,
            method: Method,
            url: &str,
            body: Option<&JsonValue>,
        ) -> anyhow::Result<(StatusCode, JsonValue)> {
            use anyhow::Context;
            let response = self
                .0
                .request(method.clone(), url)
                .opt_json(body)
                .send()
                .await
                .with_context(|| {
                    oof![url ~ url.to_string(),
                         method ~ method.clone(),
                         req ~ logging::alt(logging::opt(body.cloned()))]
                })?;
            let status = response.status();
            let response_json =
                response.json::<JsonValue>().await.with_context(|| {
                    oof![url ~ url.to_string(),
                         method ~ method.clone(),
                         req ~ logging::alt(logging::opt(body.cloned()))]
                })?;
            Ok((status, response_json))
        }

        pub async fn get_files(
            &self,
            container_id: &str,
            path: &str,
        ) -> anyhow::Result<bytes::Bytes> {
            self.get(format!(
                "http://p/v6.0.0/libpod/\
                        containers/{container_id}/archive?path=/baro/fragments/."
            ))
            .send()
            .await
            .expect_status(StatusCode::OK)
            .into_bytes()
            .await
            .with_context(|| {
                oof![s ~ "libpod/containers/archive",
                     path ~ path.to_string(),
                         container ~ container_id.to_string()]
            })
        }
    }

    #[derive(serde::Deserialize)]
    #[allow(non_snake_case)]
    pub struct CreateResponse {
        pub Id: String,
        #[serde(default)]
        pub Warnings: Vec<JsonValue>,
    }

    use traits::*;

    pub mod traits {
        use reqwest::StatusCode;
        use serde_json::Value as JsonValue;

        pub trait RequestBuilderExt {
            fn opt_json(self, j: Option<&JsonValue>) -> Self;

            fn send_and_read_json(
                self,
            ) -> impl Future<Output = reqwest::Result<(StatusCode, JsonValue)>> + Send;

            fn expect_status(
                self,
                expected: StatusCode,
            ) -> impl Future<Output = anyhow::Result<String>> + Send;
        }

        impl RequestBuilderExt for reqwest::RequestBuilder {
            fn opt_json(mut self, j: Option<&JsonValue>) -> Self {
                if let Some(j) = j {
                    self = self.json(j);
                }
                self
            }

            fn send_and_read_json(
                self,
            ) -> impl Future<Output = reqwest::Result<(StatusCode, JsonValue)>> + Send
            {
                async {
                    let response = self.send().await?;
                    let status = response.status();
                    let response_json = response.json::<JsonValue>().await?;
                    Ok((status, response_json))
                }
            }

            #[track_caller]
            fn expect_status(
                self,
                expected: StatusCode,
            ) -> impl Future<Output = anyhow::Result<String>> + Send {
                use crate::{logging, oof};
                use anyhow::Context;

                async move {
                    let response = self.send().await?;
                    let status = response.status();
                    let text = response.text().await;
                    if let Err(err) = status.expect_status(expected) {
                        return Err(err).context(oof![resp ~ logging::result(text)]);
                    }
                    Ok(text?)
                }
            }
        }

        pub trait StatusCodeExt {
            type Out;
            fn expect_status(self, expected: StatusCode) -> Self::Out;
        }

        impl StatusCodeExt for StatusCode {
            type Out = anyhow::Result<Self>;

            fn expect_status(self, expected: StatusCode) -> anyhow::Result<Self> {
                if self != expected {
                    return Err(anyhow::anyhow!(
                        "{} status not expected status {}",
                        self,
                        expected
                    ))?;
                }
                Ok(self)
            }
        }

        impl<T> StatusCodeExt for reqwest::Result<(StatusCode, T)> {
            type Out = anyhow::Result<(StatusCode, T)>;

            fn expect_status(
                self,
                expected: StatusCode,
            ) -> anyhow::Result<(StatusCode, T)> {
                match self {
                    Ok((status, t)) => Ok((status.expect_status(expected)?, t)),
                    Err(err) => Err(err.into()),
                }
            }
        }

        impl StatusCodeExt for reqwest::Result<reqwest::Response> {
            type Out = anyhow::Result<reqwest::Response>;

            fn expect_status(
                self,
                expected: StatusCode,
            ) -> anyhow::Result<reqwest::Response> {
                match self {
                    Ok(response) => {
                        response.status().expect_status(expected)?;
                        Ok(response)
                    }
                    Err(err) => Err(err.into()),
                }
            }
        }

        pub trait IntoBytes {
            type Out;
            fn into_bytes(self) -> impl Future<Output = Self::Out> + Send;
        }

        impl IntoBytes for anyhow::Result<reqwest::Response> {
            type Out = anyhow::Result<bytes::Bytes>;

            fn into_bytes(self) -> impl Future<Output = Self::Out> + Send {
                async {
                    match self {
                        Ok(r) => Ok(r.bytes().await?),
                        Err(e) => Err(e)?,
                    }
                }
            }
        }

        pub trait IntoText {
            type Out;
            fn into_text(self) -> impl Future<Output = Self::Out> + Send;
        }

        impl IntoText for anyhow::Result<reqwest::Response> {
            type Out = anyhow::Result<String>;

            fn into_text(self) -> impl Future<Output = Self::Out> + Send {
                async {
                    match self {
                        Ok(r) => Ok(r.text().await?),
                        Err(e) => Err(e)?,
                    }
                }
            }
        }

        pub trait ParseJson {
            fn parse_json<T>(self) -> anyhow::Result<T>
            where
                T: serde::de::DeserializeOwned;
        }

        impl<E> ParseJson for Result<String, E>
        where
            anyhow::Error: From<E>,
        {
            #[track_caller]
            fn parse_json<T>(self) -> anyhow::Result<T>
            where
                T: serde::de::DeserializeOwned,
            {
                use crate::oof;
                use anyhow::Context;

                match self {
                    Ok(s) => serde_json::from_str(&s).with_context(|| oof![resp ~ s]),
                    Err(e) => Err(e)?,
                }
            }
        }
    }
}

// lower-process stuff? sys/io/proc? idk rename it
pub(crate) mod misc {
    use crate::{logging, warn};
    use std::io;
    use std::process::{ExitStatus, Stdio};
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::process::Command;

    use crate::errslop::{traits::*, Oof};
    use crate::{aux, butt};

    pub type Result<T, E = Error> = std::result::Result<T, E>;

    #[derive(thiserror::Error, Debug)]
    pub enum Error {
        #[error("spawn")]
        Spawn(#[source] io::Error),
        #[error("wait")]
        Wait(#[source] io::Error),
        #[error("noio")]
        NoIo,
        #[error("exited with non-success or stdout could not be read {}", .oof)]
        BadExitOrOutputError { oof: Oof },
    }

    #[derive(Debug)]
    pub struct CompletedProcess {
        pub exit: ExitStatus,
        pub stdin: io::Result<()>,
        pub stdout: io::Result<String>,
        pub stderr: io::Result<String>,
    }

    impl CompletedProcess {
        pub fn successful_output(&self) -> Result<&String, &Self> {
            if !self.exit.success() {
                return Err(self);
            }
            let Ok(stdout) = &self.stdout else {
                return Err(self);
            };
            return Ok(stdout);
        }

        pub fn into_stdout(self) -> Result<String> {
            let Self { exit, stdin: _, stdout, stderr } = self;

            match stdout {
                Ok(stdout) if exit.success() => return Ok(stdout),
                _ => (),
            };

            return Err(Error::BadExitOrOutputError {
                oof: aux![exit ~ exit,
                          stdout ~ logging::result(stdout),
                          stderr ~ logging::result(stderr)]
                .into(),
            });
        }
    }

    use std::future::Future;

    pub mod traits {
        use super::{CompletedProcess, Result};

        pub trait ProcessExt {
            // async fn to_completion(&mut self, stdin: &[u8]) -> Result<CompletedProcess>;
            fn to_completion(
                &mut self,
                stdin: &[u8],
            ) -> impl Future<Output = Result<CompletedProcess>> + Send;
        }
    }

    impl traits::ProcessExt for Command {
        fn to_completion(
            &mut self,
            bytes: &[u8],
        ) -> impl Future<Output = Result<CompletedProcess>> {
            return async move {
                let mut child = self
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                    .map_err(Error::Spawn)?;

                let mut stdin = child.stdin.take().ok_or(Error::NoIo)?;
                let mut stdout = child.stdout.take().ok_or(Error::NoIo)?;
                let mut stderr = child.stderr.take().ok_or(Error::NoIo)?;

                let mut outbuf = String::new();
                let mut errbuf = String::new();

                let (stdin, stdout, stderr, exit) = tokio::join!(
                     biased;
                     /* move drops to ensure EOF after writing to avoid deadlock */
                     async move { stdin.write_all(&*bytes).await },
                     stdout.read_to_string(&mut outbuf),
                     stderr.read_to_string(&mut errbuf),
                     child.wait(),
                );

                let exit = exit.map_err(Error::Wait)?;
                let stdout = stdout.map(|_| outbuf);
                let stderr = stderr.map(|_| errbuf);

                return Ok(CompletedProcess { exit, stdin, stdout, stderr });
            };
        }
    }
}

pub(crate) mod db {
    use std::borrow::Cow;
    use std::sync::Arc;

    use crate::errslop::{traits::*, Oof};
    use crate::sqlext::SeqRow;
    use crate::{aux, getscols, impl_from_err};
    use crate::{
        butt, logging,
        parsing::ContentPackage,
        steamcmd::DownloadedFile,
        types::{ETag, WorkshopId},
    };

    use autodaemon::autodaemon;

    use rusqlite::{
        params,
        types::{FromSql, FromSqlResult, ToSql, ToSqlOutput, ValueRef},
        Connection, OptionalExtension, Transaction,
        TransactionBehavior::Immediate,
    };

    // use tokio::sync::Mutex;

    use kanal::Receiver as SyncReceiver;

    const SCHEMA: &'static str = include_str!("schema.sql");

    /* The #[autodaemon] macro below generates two types...
     *
     * pub enum Query { ... }
     *
     * pub struct Client { ... }
     *
     * For each function in `impl Daemon`, an async function exists on Client that forwards the
     * call to the Daemon in the Query type and returns a Receiver. The Daemon runs the it's
     * implementation of the function and the result is sent to the Receiver. */

    pub type Result<T, E = Error> = std::result::Result<T, E>;

    #[derive(thiserror::Error, Debug)]
    pub enum Error {
        #[error("rusqlite {}", .oof)]
        Rusqlite {
            #[source]
            err: rusqlite::Error,
            oof: Oof,
        },
    }

    impl_from_err!(rusqlite::Error => Error::Rusqlite);

    pub fn connect<P>(path: P) -> Result<Connection>
    where
        P: AsRef<std::path::Path>,
    {
        use rusqlite::{types::Value, OpenFlags};

        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            // The new database connection will use the multi-thread threading mode.
            // Multi-thread. In this mode, SQLite can be safely used by multiple threads provided that
            // no single database connection is used simultaneously in two or more threads.
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            // FYI this will share the underlying file handle.  So opening a new connection will not
            // actually open a new file with the operating system if an existing connection is open to
            // the same path.
            | OpenFlags::SQLITE_OPEN_SHARED_CACHE;

        let mut conn = Connection::open_with_flags(path, flags)?;

        let pragmas = [
            ("foreign_keys", &1 as &dyn ToSql),
            ("journal_mode", &"wal" as &dyn ToSql),
            ("synchronous", &"normal" as &dyn ToSql),
            ("temp_store", &"memory" as &dyn ToSql),
        ];

        for (name, value) in pragmas {
            conn.pragma_update(None, name, value)?;
        }

        // conn.set_transaction_behavior(rusqlite::TransactionBehavior::Immediate);
        // conn.set_drop_behavior(rusqlite::DropBehavior::Rollback);

        let sql = r#"SELECT count(*)
                       FROM pragma_table_info("clock")"#;
        if 0 == conn.query_row_and_then(sql, [], |r| r.get::<_, i64>(0))? {
            let tx = conn.transaction()?;
            tx.execute_batch(SCHEMA)?;
            tx.commit()?;
        }

        conn.flush_prepared_statement_cache();
        conn.set_prepared_statement_cache_capacity(64);

        Ok(conn)
    }

    #[derive(Debug)]
    pub struct Daemon {
        pub db: Connection,
        pub r: SyncReceiver<Query>,
    }

    impl Daemon {
        pub fn run_forever(&mut self) -> Result<()> {
            while let Ok(msg) = self.r.recv() {
                /* dispatch() implemented by autodaemon  */
                self.dispatch(msg);
            }
            Ok(())
        }

        fn transaction(&mut self) -> rusqlite::Result<Tx<'_>> {
            Ok(Tx { tx: self.db.transaction()? })
        }

        fn transaction_immediate(&mut self) -> rusqlite::Result<Tx<'_>> {
            Ok(Tx { tx: self.db.transaction_with_behavior(Immediate)? })
        }
    }

    #[autodaemon]
    impl Daemon {
        pub fn save_file(
            &mut self,
            item: i64,
            file: DownloadedFile,
            // size: i64,
            // etag: Arc<ETag>,
            // data: bytes::Bytes,
        ) -> Result<i64> {
            let mut tx = self.transaction_immediate()?;

            let mut pk = tx.create_timestamp()?;

            let sql = r#"INSERT INTO "file" (pk, size, etag, data)
                              VALUES (?, ?, ?, ?)
                         ON CONFLICT (etag) WHERE etag IS NOT null
                       DO UPDATE SET pk=pk
                           RETURNING pk"#;
            pk = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(
                    params![pk, file.size, &file.etag[..], file.data.as_ref()],
                    getscols![_],
                )
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk, file.size, file.etag, "...")))
                })?;

            let sql = r#"UPDATE "workshop-item"
                            SET file=?1
                          WHERE pk=?2
                      RETURNING 1"#;
            let _: i64 = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_one(params![pk, item], getscols![_])
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk, item)))
                })?;

            tx.commit()?;

            Ok(pk)
        }

        pub fn save_content_package(
            &mut self,
            mut file_pk: i64,
            p: ContentPackage,
        ) -> Result<i64> {
            let tx = self.transaction_immediate()?;

            let sql = r#"INSERT INTO "content-package" (pk, name, version)
                              VALUES (?1, ?2, ?3)
                         ON CONFLICT (pk)
                       DO UPDATE SET pk=pk
                           RETURNING pk"#;
            file_pk = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(params![file_pk, &p.name, &p.version], getscols![_])
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((file_pk, p.name, p.version)))
                })?;

            tx.commit()?;

            Ok(file_pk)
        }

        pub fn workshop_item_by_pk(&mut self, pk: i64) -> Result<Option<WorkshopItem>> {
            self.transaction()?
                .workshop_items(pk.into())
                .map(|v| v.into_iter().next())
        }

        pub fn workshop_items(&mut self, w: WorkshopId) -> Result<Vec<WorkshopItem>> {
            self.transaction()?.workshop_items(w.into())
        }

        pub fn workshop_item_file(&mut self, pk: i64) -> Result<Option<bytes::Bytes>> {
            let sql = r#"SELECT f.data
                           FROM "workshop-item" w
                      LEFT JOIN "file"          f ON f.pk = w.file
                          WHERE w.pk = ?1"#;
            Ok(self
                .transaction()?
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(params![pk], getscols![_])
                .optional()
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?
                .map(|b: Box<[u8]>| bytes::Bytes::from(b)))
        }

        pub fn build_fragment(&mut self, pk: i64) -> Result<Option<bytes::Bytes>> {
            let sql = r#"SELECT b.fragment
                           FROM "build" b
                          WHERE b.pk = ?1"#;
            Ok(self
                .transaction()?
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(params![pk], getscols![_])
                .optional()
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?
                .map(|b: Box<[u8]>| bytes::Bytes::from(b)))
        }

        pub fn upsert_workshop_item(
            &mut self,
            item: NewWorkshopItem,
        ) -> Result<(bool, i64)> {
            let mut tx = self.db.transaction_with_behavior(Immediate)?;

            let pk = create_timestamp(&mut tx)?;

            let sql = r#"INSERT INTO "workshop-item"
                                     (pk, workshopid, title,
                                      author, version)
                              VALUES (?1, ?2, ?3, ?4, ?5)
                         ON CONFLICT (workshopid, version)
                       DO UPDATE SET title=?3,
                                     author=?4
                           RETURNING pk"#;

            let row_ts = tx.prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(
                    params![
                        pk,
                        item.workshopid,
                        item.title,
                        Json(&item.authors),
                        item.version
                    ],
                    getscols![_],
                )
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk, item.workshopid, item.title, item.authors, item.version)))
                })?;

            tx.commit()?;

            let did_insert = pk == row_ts;
            Ok((did_insert, row_ts))
        }

        pub fn build_item_files(&mut self, pk: i64) -> Result<BuildItemFiles> {
            let tx = self.db.transaction()?;

            let sql = r#"SELECT w.pk, f.data
                           FROM "build-item"    i
                      LEFT JOIN "workshop-item" w ON w.pk = i.item
                      LEFT JOIN "file"          f ON f.pk = w.file
                          WHERE i.build=?1"#;
            let files: Vec<(i64, Box<[u8]>)> = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_map(params![pk], getscols![_, _])
                .and_then(|rows| rows.collect::<rusqlite::Result<Vec<_>>>())
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?;

            Ok(BuildItemFiles { files })
        }

        pub fn get_build(&mut self, pk: i64) -> Result<Option<Build>> {
            let tx = self.transaction()?;

            let sql = r#"SELECT b.name, b.exit_code, b.output, length(b.fragment),
                                (SELECT MAX(i.publish)
                                   FROM "publish-item" i
                                  WHERE i.build = b.pk)
                           FROM "build"        b
                          WHERE b.pk=?1"#;
            let build: Option<Build> = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(params![pk], getscols![_, _, _, _, _])
                .optional()
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?
                .map(|(name, exit_code, output, fragment, published)| Build {
                    pk,
                    name,
                    exit_code,
                    output,
                    items: vec![],
                    fragment: if fragment > 0 {
                        Some(BuildFragment { size: fragment })
                    } else {
                        None
                    },
                    published,
                });

            let Some(mut build) = build else {
                return Ok(None);
            };

            /* TODO we can use rowid instead of having a sort column right? */
            let sql = r#"SELECT w.pk, w.workshopid
                           FROM "build-item"    b
                           JOIN "workshop-item" w ON w.pk = b.item
                          WHERE b.build=?1
                       ORDER BY b.sort ASC"#;
            build.items = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_map(params![pk], getscols![_, _])
                .and_then(|rows| {
                    rows.map(|row| {
                        row.map(|(pk, workshopid)| BuildItem { pk, workshopid })
                    })
                    .collect::<rusqlite::Result<Vec<_>>>()
                })
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?;

            Ok(Some(build))
        }

        // pub fn build_exists(&mut self, pk: i64) -> Result<bool> {
        //     let sql = r#"SELECT 1
        //                    FROM "build"
        //                   WHERE pk = ?1"#;
        //     Ok(self
        //         .transaction()?
        //         .prepare_cached(sql)
        //         .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
        //         .query_row(params![pk], |_| Ok(()))
        //         .optional()
        //         .oof_lazy::<Error, _>(|| {
        //             aux!(sql ~ sql,
        //                  params ~ logging::dbg((pk,)))
        //         })?
        //         .is_some())
        // }

        pub fn save_build(&mut self, build: SaveBuild) -> Result<SaveBuildResult> {
            let SaveBuild { name, items } = build;

            let mut tx = self.transaction_immediate()?;

            let missing = {
                /* report missing items as those that do not match this query */
                let sql = r#"SELECT 1
                               FROM "workshop-item"
                              WHERE pk=?1
                                AND file IS NOT NULL"#;
                let mut stmt = tx
                    .prepare_cached(sql)
                    .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?;
                items
                    .iter()
                    .enumerate()
                    .filter_map(|(e, &item)| {
                        stmt.query_row(params![item], getscols![])
                            .optional()
                            .map(|o| match o {
                                Some(()) => None,
                                None => Some(item),
                            })
                            .oof_lazy::<Error, _>(|| {
                                aux!(sql ~ sql,
                                     params ~ logging::dbg((item,)))
                            })
                            .transpose()
                    })
                    .collect::<Result<Vec<_>>>()?
            };

            if missing.len() > 0 {
                return Ok(SaveBuildResult::Missing { missing });
            }

            let pk = tx.create_timestamp()?;

            let sql = r#"INSERT INTO "build"
                                     (pk, name)
                              VALUES (?1, ?2)"#;
            let n = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .execute(params![pk, name])
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk, name)))
                })
                .map(|n| debug_assert!(n == 1))?;

            let sql = r#"INSERT INTO "build-item"
                                     (build, item, sort)
                              VALUES (?1, ?2, ?3)"#;
            let mut stmt = tx
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?;

            for (e, item) in items.into_iter().enumerate() {
                stmt.execute(params![pk, item, e])
                    .oof_lazy::<Error, _>(|| {
                        aux!(sql ~ sql,
                             params ~ logging::dbg((pk, item, e)))
                    })?;
            }

            drop(stmt);

            tx.commit()?;

            Ok(SaveBuildResult::Inserted { pk })
        }

        pub fn save_build_result(&mut self, b: BuildResult) -> Result<()> {
            let tx = self.transaction_immediate()?;

            let sql = r#"UPDATE "build"
                            SET output=?2,
                                fragment=?3,
                                exit_code=?4
                          WHERE pk=?1
                      RETURNING pk"#;
            tx.prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_one(
                    params![b.pk, b.output, b.fragment, b.exit_code],
                    getscols![],
                )
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((b.pk, "...", "...", b.exit_code)))
                })?;

            Ok(tx.commit()?)
        }

        pub fn new_publish(&mut self) -> Result<NewPublish> {
            let mut tx = self.transaction_immediate()?;

            let pk = tx.create_timestamp()?;

            let sql = r#"INSERT INTO "publish" (pk)
                              VALUES (?1)"#;
            tx.prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .execute([pk])
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })
                .map(|n| debug_assert!(n == 1))?;

            let sql = r#"INSERT INTO "publish-item"
                                     (publish, build)
                              SELECT ?1, b.pk
                                FROM "build" b
                               WHERE length(fragment) > 0"#;
            tx.prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .execute([pk])
                // .and_then(|rows| rows.collect::<rusqlite::Result<Vec<_>>>())
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?;

            let sql = r#"SELECT p.build, b.fragment
                           FROM "publish-item" p
                           JOIN "build"        b ON b.pk = p.build
                          WHERE p.publish = ?1"#;
            let fragments =
                tx.prepare_cached(sql)
                    .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                    .query_map([pk], |row| {
                        Ok(NewPublishFragment {
                            build: row.get(0)?,
                            fragment: row.get(1)?,
                        })
                    })
                    .and_then(|rows| rows.collect::<rusqlite::Result<Vec<_>>>())
                    .oof_lazy::<Error, _>(|| {
                        aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                    })?;

            tx.commit()?;

            Ok(NewPublish { pk, fragments })
        }

        pub fn save_publish_result(&mut self, p: PublishResult) -> Result<()> {
            let tx = self.transaction_immediate()?;

            let sql = r#"UPDATE "publish"
                            SET output=?2,
                                exit_code=?3,
                                public_url=?4
                          WHERE pk=?1
                      RETURNING pk"#;
            tx.prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_one(params![p.pk, p.output, p.exit_code, p.public_url], getscols![])
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((p.pk, "...", p.exit_code, p.public_url)))
                })?;

            Ok(tx.commit()?)
        }

        pub fn get_publish(&mut self, pk: i64) -> Result<Option<Publish>> {
            let sql = r#"SELECT exit_code, public_url
                           FROM "publish"
                          WHERE pk = ?1"#;
            Ok(self
                .transaction()?
                .prepare_cached(sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql))?
                .query_row(params![pk], getscols![_, _])
                .optional()
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql,
                         params ~ logging::dbg((pk,)))
                })?
                .map(|(exit_code, public_url)| Publish { pk, exit_code, public_url }))
        }
    }

    struct Tx<'l> {
        pub tx: Transaction<'l>,
    }

    impl<'l> std::ops::Deref for Tx<'l> {
        type Target = Transaction<'l>;

        fn deref(&self) -> &Transaction<'l> {
            &self.tx
        }
    }

    #[derive(Debug)]
    enum WorkshopItemFilter {
        ByPk(i64),
        ByWorkshopId(WorkshopId),
    }

    impl From<i64> for WorkshopItemFilter {
        fn from(v: i64) -> Self {
            Self::ByPk(v)
        }
    }

    impl From<WorkshopId> for WorkshopItemFilter {
        fn from(v: WorkshopId) -> Self {
            Self::ByWorkshopId(v)
        }
    }

    impl<'l> Tx<'l> {
        fn commit(self) -> rusqlite::Result<()> {
            self.tx.commit()
        }

        fn create_timestamp(&mut self) -> rusqlite::Result<i64> {
            create_timestamp(&mut self.tx)
        }

        fn workshop_items(
            &mut self,
            f: WorkshopItemFilter,
        ) -> Result<Vec<WorkshopItem>> {
            let where_col = match f {
                WorkshopItemFilter::ByPk(_) => "pk",
                WorkshopItemFilter::ByWorkshopId(_) => "workshopid",
            };
            let sql = format!(
                r#"SELECT i.pk, i.workshopid, i.title, i.author, i.version
                        , f.pk, f.size
                        , p.name, p.version
                     FROM "workshop-item" i
                LEFT JOIN "file" f            ON f.pk = i.file
                LEFT JOIN "content-package" p ON p.pk = i.file
                    WHERE i.{where_col} = ?
                 ORDER BY i.pk DESC"#
            );
            let param = match &f {
                WorkshopItemFilter::ByPk(v) => v as &dyn ToSql,
                WorkshopItemFilter::ByWorkshopId(v) => v as &dyn ToSql,
            };
            Ok(self
                .tx
                .prepare_cached(&sql)
                .oof_lazy::<Error, _>(|| aux!(sql ~ sql.clone()))?
                .query_map([param], |row| {
                    WorkshopItem::from_row(&mut SeqRow::from(row))
                })
                .and_then(|rows| rows.collect::<rusqlite::Result<Vec<_>>>())
                .oof_lazy::<Error, _>(|| {
                    aux!(sql ~ sql.clone(),
                         params ~ logging::dbg((f,)))
                })?)
        }
    }

    pub fn create_timestamp(tx: &mut Transaction) -> rusqlite::Result<i64> {
        let ms = jiff::Timestamp::now().as_millisecond();

        let shift = ms.checked_shl(4).expect("timestamp too big");

        return tx
            .prepare_cached(
                r#"UPDATE "clock"
                      SET ts = max((SELECT ts + 1 FROM "clock"), ?)
                RETURNING ts"#,
            )?
            .query_one([shift], |r| r.get(0));
    }

    #[derive(Debug, Clone, serde::Serialize)]
    pub struct WorkshopItem {
        pub pk: i64,
        pub workshopid: WorkshopId,
        pub title: String,
        pub authors: Vec<String>,
        pub version: i64,
        pub file: Option<ItemFile>,
        pub content: Option<FileContentPackage>,
    }

    impl WorkshopItem {
        fn from_row(s: &mut SeqRow<'_>) -> rusqlite::Result<Self> {
            Ok(WorkshopItem {
                pk: s.get()?,
                workshopid: s.get()?,
                title: s.get()?,
                authors: s.get().map(|Json(a)| a)?,
                version: s.get()?,
                file: match (s.get()?, s.get()?) {
                    (Some(pk), Some(size)) => Some(ItemFile { pk, size }),
                    _ => None,
                },
                content: match (s.get()?, s.get()?) {
                    (Some(name), Some(version)) => {
                        Some(FileContentPackage { name, version })
                    }
                    _ => None,
                },
            })
        }
    }

    #[derive(Debug, Clone, serde::Serialize)]
    pub struct ItemFile {
        #[serde(skip)]
        pub pk: i64,
        pub size: i64,
        // pub etag: ETag,
    }

    /* from <contentpackage> */
    #[derive(Debug, Clone, serde::Serialize)]
    pub struct FileContentPackage {
        pub name: String,
        pub version: String,
    }

    #[derive(Debug, Clone)]
    pub struct NewWorkshopItem {
        pub workshopid: WorkshopId,
        pub title: String,
        pub authors: Vec<String>,
        pub version: i64,
    }

    impl ToSql for WorkshopId {
        fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
            self.as_str().to_sql()
        }
    }

    #[derive(Debug, Clone, serde::Serialize)]
    pub struct Build {
        pub pk: i64,
        pub name: String,
        pub items: Vec<BuildItem>,
        pub exit_code: Option<i64>,
        pub output: Option<String>,
        pub fragment: Option<BuildFragment>,
        pub published: Option<i64>,
    }

    #[derive(Debug, Clone, serde::Serialize)]
    pub struct BuildFragment {
        pub size: i64,
    }

    #[derive(Debug, Clone, serde::Serialize)]
    pub struct BuildItem {
        pub pk: i64,
        pub workshopid: WorkshopId,
    }

    #[derive(Debug, Clone, serde::Deserialize)]
    pub struct SaveBuild {
        // pub pk: Option<i64>,
        pub name: String,
        pub items: Vec<i64>,
    }

    #[derive(Debug, Clone)]
    pub enum SaveBuildResult {
        Inserted { pk: i64 },
        Missing { missing: Vec<i64> },
    }

    #[derive(Debug, Clone)]
    pub struct BuildResult {
        pub pk: i64,
        pub exit_code: i64,
        pub output: String,
        pub fragment: Vec<u8>,
    }

    #[derive(Debug, Clone)]
    pub struct BuildItemFiles {
        pub files: Vec<(i64, Box<[u8]>)>,
    }

    pub struct NewPublish {
        pub pk: i64,
        pub fragments: Vec<NewPublishFragment>,
    }

    pub struct NewPublishFragment {
        pub build: i64,
        pub fragment: Box<[u8]>,
    }

    #[derive(Debug)]
    pub struct PublishResult {
        pub pk: i64,
        pub public_url: String,
        pub exit_code: i64,
        pub output: String,
    }

    #[derive(Debug, Clone, serde::Serialize)]
    pub struct Publish {
        pub pk: i64,
        pub public_url: String,
        pub exit_code: Option<i64>,
    }

    impl FromSql for WorkshopId {
        fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
            let s = String::column_result(value)?;
            WorkshopId::try_from(s).map_err(rusqlite::types::FromSqlError::other)
        }
    }

    #[derive(Debug)]
    struct Json<T>(T);

    impl<T> ToSql for Json<T>
    where
        T: serde::Serialize,
    {
        fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
            serde_json::to_string(&self.0)
                .map(ToSqlOutput::from)
                .map_err(|err| rusqlite::Error::ToSqlConversionFailure(err.into()))
        }
    }

    impl<T> FromSql for Json<T>
    where
        for<'d> T: serde::Deserialize<'d>,
    {
        fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
            serde_json::from_str(value.as_str()?)
                .map(Json)
                .map_err(rusqlite::types::FromSqlError::other)
        }
    }
}

pub(crate) mod steamweb {
    use crate::httpreq;
    use crate::logging;
    use crate::parsing::{
        WorkshopCollectionPage, WorkshopItemChangelog, WorkshopItemFileDetails,
    };
    use crate::types::WorkshopId;

    use crate::{
        aux,
        errslop::{traits::*, Oof},
        impl_from_err, oof,
    };

    pub type Result<T, E = Error> = std::result::Result<T, E>;

    #[derive(Debug, thiserror::Error)]
    pub enum Error {
        #[error("httpreq error {}", .oof)]
        Httpreq {
            #[source]
            err: httpreq::Error,
            oof: Oof,
        },
        #[error("workshop content parse error {} ➭ {}", .oof, .err)]
        Parse { err: &'static str, oof: Oof },
    }

    impl_from_err!(httpreq::Error => Error::Httpreq);
    impl_from_err!(&'static str => Error::Parse);

    #[derive(Debug, Clone)]
    pub struct Client(httpreq::Client);

    impl From<httpreq::Client> for Client {
        fn from(o: httpreq::Client) -> Self {
            Self(o)
        }
    }

    impl std::ops::Deref for Client {
        type Target = httpreq::Client;

        fn deref(&self) -> &httpreq::Client {
            &self.0
        }
    }

    impl Client {
        pub async fn acquire<'l>(&'l self) -> Option<BorrowedClient<'l>> {
            self.0.acquire().await.map(BorrowedClient)
        }
    }

    #[derive(Debug)]
    pub struct BorrowedClient<'l>(httpreq::BorrowedClient<'l>);

    impl<'l> std::ops::Deref for BorrowedClient<'l> {
        type Target = httpreq::BorrowedClient<'l>;

        fn deref(&self) -> &httpreq::BorrowedClient<'l> {
            &self.0
        }
    }

    impl<'l> BorrowedClient<'l> {
        pub async fn changelog(
            &self,
            steamcommunity: &reqwest::Url,
            workshopid: &WorkshopId,
        ) -> Result<WorkshopItemChangelog> {
            let url = changelog_url(steamcommunity, workshopid);
            Ok(self
                .get_text(&url)
                .await?
                .parse::<_>()
                .oof_lazy::<Error, _>(|| aux![url ~ url.clone()])?)
        }

        pub async fn filedetails(
            &self,
            steamcommunity: &reqwest::Url,
            workshopid: &WorkshopId,
        ) -> Result<WorkshopItemFileDetails> {
            let url = filedetails_url(steamcommunity, workshopid);
            Ok(self
                .get_text(&url)
                .await?
                .parse::<_>()
                .oof_lazy::<Error, _>(|| aux![url ~ url.clone()])?)
        }

        pub async fn collection(
            &self,
            steamcommunity: &reqwest::Url,
            workshopid: &WorkshopId,
        ) -> Result<WorkshopCollectionPage> {
            let url = filedetails_url(steamcommunity, workshopid);
            Ok(self
                .get_text(&url)
                .await?
                .parse::<_>()
                .oof_lazy::<Error, _>(|| aux![url ~ url.clone()])?)
        }
    }

    pub fn filedetails_url(steamcommunity: &reqwest::Url, id: &WorkshopId) -> String {
        format!("{steamcommunity}sharedfiles/filedetails/?id={id}")
    }

    pub fn changelog_url(steamcommunity: &reqwest::Url, id: &WorkshopId) -> String {
        format!("{steamcommunity}sharedfiles/filedetails/changelog/{id}")
    }

    #[test]
    fn test_urls() {
        let workshopid = "1234".to_owned().try_into().unwrap();

        /* with trailing slash */
        let steamcommunity = "http://example.com/".parse().unwrap();
        assert_eq!(
            changelog_url(&steamcommunity, &workshopid),
            "http://example.com/sharedfiles/filedetails/changelog/1234"
        );

        /* without trailing slash */
        let steamcommunity = "https://example.com".parse().unwrap();
        assert_eq!(
            filedetails_url(&steamcommunity, &workshopid),
            "https://example.com/sharedfiles/filedetails/?id=1234"
        );
    }
}

pub(crate) mod httpreq {
    use std::sync::Arc;
    use std::time::Duration;
    use tokio::sync::{Semaphore, SemaphorePermit};

    use crate::errslop::{traits::*, Oof};
    use crate::logging;
    use crate::types::WorkshopId;
    use crate::{aux, impl_from_err};

    #[derive(Debug, thiserror::Error)]
    pub enum Error {
        #[error("reqwest error {}", .oof)]
        Reqwest {
            #[source]
            err: reqwest::Error,
            oof: Oof,
        },
    }

    impl_from_err!(reqwest::Error => Error::Reqwest);

    pub type Result<T, E = Error> = std::result::Result<T, E>;

    pub fn builder() -> Builder<'static> {
        Builder {
            inner: reqwest::Client::builder(),
            limit: 32,
            user_agent: "",
            unix_socket: "",
            read_timeout: None,
        }
    }

    pub struct Builder<'a> {
        inner: reqwest::ClientBuilder,
        limit: u8,
        read_timeout: Option<Duration>,
        user_agent: &'a str,
        unix_socket: &'a str,
    }

    impl<'a> Builder<'a> {
        pub fn limit(mut self, limit: u8) -> Self {
            self.limit = limit;
            self
        }

        pub fn read_timeout(mut self, v: Option<Duration>) -> Self {
            self.read_timeout = v;
            self
        }

        pub fn user_agent(mut self, s: &'a str) -> Self {
            self.user_agent = s;
            self
        }

        pub fn unix_socket(mut self, s: &'a str) -> Self {
            self.unix_socket = s;
            self
        }

        pub fn build(self) -> anyhow::Result<Client> {
            use crate::oof;
            use anyhow::Context;

            let Self { mut inner, limit, user_agent, unix_socket, read_timeout } = self;

            inner = inner.connect_timeout(Duration::from_secs(20));

            if let Some(read_timeout) = read_timeout {
                inner = inner.read_timeout(read_timeout);
            }

            if !user_agent.is_empty() {
                inner = inner.user_agent(user_agent);
            }
            if !unix_socket.is_empty() {
                inner = inner.unix_socket(unix_socket);
            }
            let http = inner.build().with_context(|| {
                oof![s ~ "init http client",
                     useragent ~ user_agent.to_string(),
                     unix ~ unix_socket.to_string()]
            })?;
            let limit = Arc::new(Semaphore::new(limit.into()));
            Ok(Client { http, limit })
        }
    }

    #[derive(Debug, Clone)]
    pub struct Client {
        /* reqwest doesn't limit the number of concurrent connections in its pool,
         * so we'll try to limit ourselves i guess ... */
        limit: Arc<Semaphore>,
        http: reqwest::Client,
    }

    #[derive(Debug)]
    pub struct BorrowedClient<'l> {
        #[allow(unused)]
        permit: SemaphorePermit<'l>,
        http: reqwest::Client,
    }

    impl<'l> std::ops::Deref for BorrowedClient<'l> {
        type Target = reqwest::Client;

        fn deref(&self) -> &reqwest::Client {
            &self.http
        }
    }

    impl Client {
        pub fn new(http: reqwest::Client, limit: u8) -> Self {
            let limit = Arc::new(Semaphore::new(limit.into()));
            Self { http, limit }
        }

        pub async fn acquire<'l>(&'l self) -> Option<BorrowedClient<'l>> {
            self.limit
                .acquire()
                .await
                .ok()
                .map(|permit| BorrowedClient { permit, http: self.http.clone() })
        }
    }

    impl<'l> BorrowedClient<'l> {
        pub async fn get_text(&self, url: &str) -> Result<String> {
            let response: reqwest::Response = self
                .http
                .get(url)
                .send()
                .await
                .oof_lazy::<Error, _>(|| aux!(url ~ url.to_string()))?;

            if let Err(err) = response.error_for_status_ref() {
                let body = response.text().await;
                return Err(Error::Reqwest {
                    err: err,
                    oof: aux!(url ~ url.to_string(),
                              resp ~ logging::result(body))
                    .into(),
                })?;
            }

            Ok(response
                .text()
                .await
                .oof_lazy::<Error, _>(|| aux!(url ~ url.to_string()))?)
        }
    }
}

pub(crate) mod parsing {
    use std::borrow::Cow;
    use std::str::FromStr;

    use dom_query::{Document, Matcher};

    use crate::types::{is_workshopid_char, WorkshopId};
    use crate::warn;

    #[cfg(test)]
    const EXAMPLE_FILELIST: &'static str = r#"<?xml version="1.0" encoding="utf-8"?>
<contentpackage name="Dunwall V" modversion="1.0.2" corepackage="False" gameversion="1.3.0.1" expectedhash="205BDCF97B2C6316CA6115027E1E27DF">
  <Item file="%ModDir%/Content/items.xml" />
  <Submarine file="%ModDir%/Dunwall V.sub" />
</contentpackage>"#;

    #[cfg(test)]
    const EXAMPLE_COLLECTION: &'static str =
        include_str!("../testing/collection-3394099945.html");

    #[cfg(test)]
    const EXAMPLE_ITEM: &'static str = include_str!("../testing/item-3153737715.html");

    #[cfg(test)]
    const EXAMPLE_ITEM_2: &'static str =
        include_str!("../testing/item-2532991202.html");

    #[cfg(test)]
    const EXAMPLE_CHANGELOG: &'static str =
        include_str!("../testing/changelog-3153737715.html");

    #[test]
    fn test_filelist() {
        use dom_query::Document;

        let doc = Document::from(EXAMPLE_FILELIST);
        let el = doc.select("contentpackage");
        assert_eq!(Some("Dunwall V"), el.attr("name").as_deref());
        assert_eq!(Some("1.0.2"), el.attr("modversion").as_deref());
    }

    #[test]
    fn test_file_details() {
        let res = EXAMPLE_ITEM.parse::<WorkshopItemFileDetails>();
        assert_eq!(
            res,
            Ok(WorkshopItemFileDetails {
                appid: "602960".to_string(),
                workshopid: "3153737715".parse().unwrap(),
                title: "Soundproof Walls 2.0".to_string(),
                authors: vec!["Plag".to_string()],
            })
        );

        let res = EXAMPLE_ITEM_2.parse::<WorkshopItemFileDetails>();
        assert_eq!(
            res,
            Ok(WorkshopItemFileDetails {
                appid: "602960".to_string(),
                workshopid: "2532991202".parse().unwrap(),
                title: "DynamicEuropa".to_string(),
                authors: vec![
                    "hUbert 2".to_string(),
                    "_]|M|[_".to_string(),
                    "MasonMachineGuns".to_string(),
                ],
            })
        );

        let res = EXAMPLE_COLLECTION.parse::<WorkshopItemFileDetails>();
        dbg!(&res);
        assert_eq!(
            res,
            Ok(WorkshopItemFileDetails {
                appid: "602960".to_string(),
                workshopid: "3394099945".parse().unwrap(),
                title: "Casual Ironman Campaign".to_string(),
                authors: vec!["_]|M|[_".to_string(),],
            })
        );
    }

    #[test]
    fn test_changelog() {
        let res = EXAMPLE_CHANGELOG.parse::<WorkshopItemChangelog>();
        assert!(res.is_ok());
        assert_eq!(res.unwrap().latest_timestamp, 1758783215);
    }

    #[test]
    fn test_collection() {
        let res = EXAMPLE_COLLECTION.parse::<WorkshopCollectionPage>();
        assert!(res.is_ok());
        assert_eq!(
            &res.unwrap().items[..3],
            vec![
                "2559634234".parse().unwrap(),
                "3153737715".parse().unwrap(),
                "3218219821".parse().unwrap(),
            ]
        );

        let res = EXAMPLE_ITEM.parse::<WorkshopCollectionPage>();
        assert!(res.is_err());
    }

    #[test]
    fn test_workshopid_from_url_string() {
        assert_eq!(
            "3166241648".parse().ok(),
            workshopid_from_url(
                "https://steamcommunity.com/sharedfiles/filedetails/?id=3166241648"
            )
        );
        assert_eq!(
            "3166241648".parse().ok(),
            workshopid_from_url(
                "https://steamcommunity.com/workshop/filedetails/?id=3166241648"
            )
        );
        assert_eq!(
            "3166241648".parse().ok(),
            workshopid_from_url(
                "https://steamcommunity.com/sharedfiles/filedetails/?id=3166241648&something-wacky"
            )
        );
        assert_eq!(
            None,
            workshopid_from_url(
                "https://steamcommunity.com/sharedfiles/filedetails/?id="
            )
        );
        assert_eq!(None, workshopid_from_url("12345"));
    }

    #[derive(Debug, Clone, PartialEq)]
    pub struct ContentPackage {
        pub name: String,
        pub version: String,
    }

    impl ContentPackage {
        /* TODO support missing keys under the contentpackage.
         *
         * Use Option<String> as ContentPackage members and store empty strings
         * in the database? Dunno if that will ever come up. */
        pub fn from_filelist(s: &str) -> Option<Self> {
            let doc = Document::from(s);
            let el = doc.try_select("contentpackage")?;
            ContentPackage {
                name: el.attr("name").as_deref()?.to_string(),
                version: el
                    .attr("modversion")
                    .as_deref()
                    .unwrap_or_default()
                    .to_string(),
            }
            .into()
        }
    }

    impl FromStr for ContentPackage {
        type Err = &'static str;

        fn from_str(s: &str) -> Result<Self, Self::Err> {
            let doc = Document::from(s);
            let el = doc
                .try_select("contentpackage")
                .ok_or("no contentpackage element")?;
            Ok(ContentPackage {
                name: el
                    .attr("name")
                    .as_deref()
                    .ok_or("no name attribute")?
                    .to_string(),
                version: el
                    .attr("modversion")
                    .as_deref()
                    .unwrap_or_default()
                    .to_string(),
            })
        }
    }

    #[test]
    fn test_content_package() {
        const XAN: &'static str =
            include_str!("../testing/contentpackage-2108010462.xml");
        assert_eq!(
            Ok(ContentPackage {
                name: "XanMonsters".to_string(),
                version: "".to_string()
            }),
            XAN.parse(),
        );
    }

    #[derive(Debug, PartialEq)]
    pub struct WorkshopItemFileDetails {
        pub appid: String,
        pub workshopid: WorkshopId,
        pub title: String,
        pub authors: Vec<String>,
    }

    impl FromStr for WorkshopItemFileDetails {
        type Err = &'static str;

        fn from_str(s: &str) -> Result<Self, Self::Err> {
            let data_appid = css("[data-appid]");
            let workshop_url = css(
                r#".sectionTabs a[href^="https://steamcommunity.com/sharedfiles/filedetails/?id="]"#,
            );
            let title = css(".workshopItemTitle");
            let creator = css(".creatorsBlock .friendBlockContent");
            let ratings = css(".numRatings");
            let stats = css(".stats_table");
            let td = css("td");

            let doc = Document::from(s);

            let appid = doc
                .select_matcher(&data_appid)
                .first()
                .attr("data-appid")
                .ok_or("no appid")
                .map(String::from)?;

            let workshopid = doc
                .select_matcher(&workshop_url)
                .first()
                .attr("href")
                .and_then(|s| workshopid_from_url(&s))
                .ok_or("no workshop url")?;

            let title = doc
                .select_matcher(&title)
                .first()
                .immediate_text()
                .trim()
                .into();

            let authors = doc
                .select_matcher(&creator)
                .iter()
                .map(|s| s.immediate_text().trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();

            // let num_ratings = try_i64_from_formatted(
            //     doc.select_matcher(&ratings)
            //         .first()
            //         .immediate_text()
            //         .trim()
            //         .to_string(),
            // );

            // let mut unique_visits: Option<i64> = None;
            // let mut current_subs: Option<i64> = None;
            // let mut current_favs: Option<i64> = None;

            // doc.select(".stats_table tr").iter().for_each(|tr| {
            //     let mut r = tr.select_matcher(&td).iter();

            //     let Some(value) = r
            //         .next()
            //         .map(|s| s.immediate_text().trim().to_string())
            //         .and_then(try_i64_from_formatted)
            //     else {
            //         return;
            //     };

            //     let Some(name) = r.next().map(|s| s.immediate_text()) else {
            //         return;
            //     };

            //     if name.eq_ignore_ascii_case("unique visitors") {
            //         unique_visits = Some(value)
            //     } else if name.eq_ignore_ascii_case("current subscribers") {
            //         current_subs = Some(value)
            //     } else if name.eq_ignore_ascii_case("current favorites") {
            //         current_favs = Some(value)
            //     }
            // });

            Ok(WorkshopItemFileDetails { appid, workshopid, title, authors })
        }
    }

    // fn try_i64_from_formatted(mut s: String) -> Option<i64> {
    //     s.retain(|c: char| c != ',');
    //     s.parse().ok()
    // }

    #[derive(Debug)]
    pub struct WorkshopItemChangelog {
        pub appid: String,
        pub workshopid: WorkshopId,
        pub latest_timestamp: i64,
        // pub timestamps: Box<[i64]>,
    }

    impl FromStr for WorkshopItemChangelog {
        type Err = &'static str;

        fn from_str(s: &str) -> Result<Self, &'static str> {
            let data_appid = css("[data-appid]");
            let workshop_url = css(
                r#".sectionTabs a[href^="https://steamcommunity.com/sharedfiles/filedetails/?id="]"#,
            );
            let timestamp = css(".workshopAnnouncement p[id]");

            let doc = Document::from(s);

            let latest_timestamp = doc
                .select_matcher(&timestamp)
                .first()
                .attr("id")
                .ok_or("no timestamp")
                .and_then(|id| id.parse::<i64>().map_err(|_| "parse i64"))?;

            let appid = doc
                .select_matcher(&data_appid)
                .first()
                .attr("data-appid")
                .ok_or("no appid")
                .map(String::from)?;

            let workshopid = doc
                .select_matcher(&workshop_url)
                .first()
                .attr("href")
                .and_then(|s| workshopid_from_url(&s))
                .ok_or("no workshop url")?;

            Ok(WorkshopItemChangelog { appid, workshopid, latest_timestamp })
        }
    }

    #[derive(Debug)]
    pub struct WorkshopCollectionPage {
        pub items: Box<[WorkshopId]>,
    }

    impl FromStr for WorkshopCollectionPage {
        type Err = &'static str;

        fn from_str(s: &str) -> Result<Self, &'static str> {
            let items = css(".collectionItem");
            let anchors = css(
                r#"a[href^="https://steamcommunity.com/sharedfiles/filedetails/?id="]"#,
            );

            let doc = Document::from(s);

            let items = doc
                .select_matcher(&items)
                .iter()
                .filter_map(|el| {
                    let href = el.select_matcher(&anchors).first().attr("href")?;
                    workshopid_from_url(&href)
                    // let Some(id) =  else {
                    //     warn!("no workshop id in href";
                    //           "href" => &href);
                    //     return None;
                    // };
                    // Some(id.to_string())
                })
                .collect::<Box<[_]>>();

            if items.is_empty() {
                return Err("no collection items found");
            }

            Ok(WorkshopCollectionPage { items })
        }
    }

    pub fn workshopid_from_url(url: &str) -> Option<WorkshopId> {
        const PREFIX: &'static str =
            "https://steamcommunity.com/sharedfiles/filedetails/?id=";

        const ALSO_PREFIX: &'static str =
            "https://steamcommunity.com/workshop/filedetails/?id=";

        let mut tail = url
            .strip_prefix(PREFIX)
            .or_else(|| url.strip_prefix(ALSO_PREFIX))?;

        if let Some(i) = tail.find(|c| !is_workshopid_char(c)) {
            tail = tail.get(0..i)?;
        }

        WorkshopId::try_from(tail.to_string()).ok()
    }

    fn css(s: &'static str) -> Matcher {
        Matcher::new(s).unwrap()
    }
}

pub(crate) mod sqlext {
    use rusqlite::types::FromSql;

    #[macro_export]
    macro_rules! getscols {
        ($( $t:ty ),*) => {
            |row| {
                let mut _s = $crate::sqlext::SeqRow::from(row);
                Ok(($(_s.get::<$t>()?),*))
            }
        }
    }

    pub struct SeqRow<'a> {
        pub row: &'a rusqlite::Row<'a>,
        pub seq: usize,
    }

    impl<'a> SeqRow<'a> {
        pub fn get<T: FromSql>(&mut self) -> rusqlite::Result<T> {
            let res = self.row.get::<_, T>(self.seq);
            self.seq += 1;
            res
        }
    }

    impl<'a> From<&'a rusqlite::Row<'a>> for SeqRow<'a> {
        fn from(row: &'a rusqlite::Row<'a>) -> Self {
            SeqRow { row, seq: 0 }
        }
    }
}

pub(crate) mod errslop {
    use std::fmt;
    use std::panic::Location;

    use traits::*;

    #[derive(Debug)]
    pub struct Oof {
        loc: &'static Location<'static>,
        aux: Aux,
    }

    impl From<Aux> for Oof {
        #[track_caller]
        fn from(aux: Aux) -> Self {
            let loc = Location::caller();
            Oof { loc, aux }
        }
    }

    impl Default for Oof {
        #[track_caller]
        fn default() -> Self {
            let loc = Location::caller();
            Oof { loc, aux: Default::default() }
        }
    }

    impl fmt::Display for Oof {
        fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
            use crate::ansi;

            write!(f, "{}", ansi::italic.fg(self.loc),)?;
            for item in self.aux.iter() {
                write!(
                    f,
                    "\n«{}» {}",
                    // " «{}» {}",
                    ansi::blue.fg(item.label /*format!("{:>12}", item.label)*/),
                    item.value
                )?;
            }
            Ok(())
        }
    }

    pub type Aux = Box<[AuxItem]>;

    pub trait Auxable: std::fmt::Display + Send + Sync + 'static {}

    impl<T> Auxable for T where T: std::fmt::Display + Send + Sync + 'static {}

    pub struct AuxItem {
        pub label: &'static str,
        pub value: Box<dyn Auxable>,
    }

    impl fmt::Debug for AuxItem {
        fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
            f.debug_struct("AuxItem")
                .field("label", &self.label)
                // .field("value", &r#"¯\_(ツ)_/¯"#)
                .finish()
        }
    }

    #[derive(Debug)]
    pub struct BigOof<T> {
        pub err: T,
        pub oof: Oof,
    }

    impl<T> From<T> for BigOof<T> {
        #[track_caller]
        fn from(err: T) -> Self {
            BigOof { err, oof: Default::default() }
        }
    }

    #[macro_export]
    macro_rules! oof {
        ($($tt:tt)*) => {
            crate::errslop::Oof::from(crate::aux![$($tt)*])
        };
    }

    #[macro_export]
    macro_rules! aux {
        ($($k:ident ~ $v:expr),* $(,)?) => {
            Box::new([
                // $($crate::errslop::AuxItem
                //     { label: stringify!($t), value: Box::new($t) },)*
                // $(
                $($crate::errslop::AuxItem
                    { label: stringify!($k), value: Box::new($v) },)*
                // )*
            ]) as Box<[$crate::errslop::AuxItem]>
        };
    }

    pub mod traits {
        use super::{Aux, BigOof};

        pub trait ErrOof<T, Y> {
            // depr i guess cause aux!() is not zero cost
            fn oof<E>(self, _: Aux) -> Result<T, E>
            where
                E: From<BigOof<Y>>;

            fn oof_lazy<E, F>(self, _: F) -> Result<T, E>
            where
                F: FnOnce() -> Aux,
                E: From<BigOof<Y>>;
        }
    }

    impl<T, Y> ErrOof<T, Y> for Result<T, Y> {
        #[track_caller]
        fn oof<E>(self, aux: Aux) -> Result<T, E>
        where
            E: From<BigOof<Y>>,
        {
            let loc = Location::caller();
            let oof = Oof { loc, aux };
            self.map_err(move |err| E::from(BigOof { err, oof }))
        }

        #[track_caller]
        fn oof_lazy<E, F>(self, f: F) -> Result<T, E>
        where
            F: FnOnce() -> Aux,
            E: From<BigOof<Y>>,
        {
            match self {
                Ok(v) => Ok(v),
                Err(err) => {
                    let loc = Location::caller();
                    let oof = Oof { loc, aux: f() };
                    Err(E::from(BigOof { err, oof }))
                }
            }
        }
    }

    #[macro_export]
    macro_rules! impl_from_err {
        ($from:ty => $enum:ident :: $variant:ident) => {
            impl From<$from> for $enum {
                #[track_caller]
                fn from(err: $from) -> Self {
                    $crate::errslop::BigOof::from(err).into()
                }
            }

            impl From<$crate::errslop::BigOof<$from>> for $enum {
                #[track_caller]
                fn from(oof: $crate::errslop::BigOof<$from>) -> Self {
                    let $crate::errslop::BigOof { err, oof } = oof;
                    $enum::$variant { err, oof }
                }
            }
        };
    }
}
