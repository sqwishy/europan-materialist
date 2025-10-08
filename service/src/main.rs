#![allow(unused_variables)]
#![allow(unused_imports)]
#![allow(unused_mut)]
#![allow(dead_code)]

use anyhow::{Context, Result};
use std::sync::Arc;

pub mod ansi;
pub mod logging;

// use logging::{butt, crit};

fn main() {
    // let (sig_tx, sig_rx) = async_channel::bounded::<()>(8);

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

    // let body = include_str!("/tmp/workshop-collection-mixed.json");
    // let r: steamapi::CollectionDetailsResponse = serde_json::from_str(&body).expect("parse json");
    // dbg!(r);

    // let body = include_str!("/tmp/workshop-item-details-mixed.json");
    // let r: steamapi::PublishedFileDetailsResponse =
    //     serde_json::from_str(&body).expect("parse json");
    // dbg!(r);

    // dbg!(new_session_string());

    match rt.block_on(async_main()).context("async_main") {
        Ok(()) => (),
        Err(err) => {
            crit!("oof");
            crit!("{:?}", err);
        }
    }
}

#[derive(Debug)]
pub struct SessionString(String);

impl std::ops::Deref for SessionString {
    type Target = String;

    fn deref(&self) -> &String {
        &self.0
    }
}

fn new_session_string() -> SessionString {
    const CHARS: &[u8] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZabcdefghjkmnpqrstvwxyz";

    SessionString(
        std::iter::repeat_with(|| {
            *fastrand::choice(CHARS)
                .unwrap(/* this is impossible, chars is non-empty */) as char
        })
        .take(12)
        .collect(),
    )
}

pub mod steamapi {
    use facet::Facet;
    use serde::Deserialize;

    #[derive(Facet, Debug, Deserialize)]
    pub struct Response<T> {
        pub response: T,
    }

    /**/

    pub type PublishedFileDetailsResponse<'a> = Response<PublishedFileDetailsResults<'a>>;

    #[derive(Facet, Debug, Deserialize)]
    pub struct PublishedFileDetailsResults<'a> {
        pub result: i32,
        #[serde(borrow)]
        pub publishedfiledetails: Vec<PublishedFileDetailsResult<'a>>,
    }

    #[derive(Facet, Debug, Deserialize)]
    pub struct PublishedFileDetailsResult<'a> {
        pub result: i32,
        #[serde(flatten)]
        #[serde(borrow)]
        pub details: Option<PublishedFileDetails<'a>>,
    }

    #[derive(Facet, Debug, Deserialize)]
    pub struct PublishedFileDetails<'a> {
        pub publishedfileid: &'a str,
        pub consumer_app_id: u64,
        /* player steamid */
        pub creator: &'a str,
        pub title: &'a str,
        pub preview_url: &'a str,
        pub time_created: u64,
        pub time_updated: u64,
    }

    /**/

    pub type CollectionDetailsResponse<'a> = Response<CollectionDetailsResults<'a>>;

    #[derive(Facet, Debug, Deserialize)]
    pub struct CollectionDetailsResults<'a> {
        pub result: i32,
        #[serde(borrow)]
        pub collectiondetails: Vec<CollectionDetails<'a>>,
    }

    #[derive(Facet, Debug, Deserialize)]
    pub struct CollectionDetails<'a> {
        pub result: i32,
        pub publishedfileid: &'a str,
        #[serde(borrow)]
        pub children: Option<Vec<CollectionDetailsChildren<'a>>>,
    }

    #[derive(Facet, Debug, Deserialize)]
    pub struct CollectionDetailsChildren<'a> {
        pub publishedfileid: &'a str,
        pub sortorder: u32,
    }

    /**/

    pub type PlayerSummariesResponse<'a> = Response<PlayerSummariesResults<'a>>;

    #[derive(Facet, Debug, Deserialize)]
    pub struct PlayerSummariesResults<'a> {
        pub result: i32,
        #[serde(borrow)]
        pub players: Vec<PlayerSummary<'a>>,
    }

    #[derive(Facet, Debug, Deserialize)]
    pub struct PlayerSummary<'a> {
        pub steamid: &'a str,
        pub personaname: &'a str,
        pub profileurl: &'a str,
        pub avatar: &'a str,
        pub avatarmedium: &'a str,
        pub avatarmediumfull: &'a str,
    }

    #[derive(Debug)]
    pub struct Client {
        http: reqwest::Client,
    }

    impl std::ops::Deref for Client {
        type Target = reqwest::Client;

        fn deref(&self) -> &reqwest::Client {
            &self.http
        }
    }

    impl Client {
        pub fn new() -> Self {
            let http = reqwest::Client::new();
            Self { http }
        }

        pub async fn request_to_parts(
            &self,
            req: reqwest::RequestBuilder,
        ) -> anyhow::Result<Parts> {
            use anyhow::Context;

            let rep = req.send().await.context("send")?;
            let status = rep.status();
            let body = rep.bytes().await.context("read")?;

            if !status.is_success() {
                return Err(anyhow::anyhow!(display_response(status, &body)));
            }

            Ok(Parts { status, body })
        }

        pub fn request_published_file_details(&self, workshopid: &str) -> reqwest::RequestBuilder {
            let url =
                "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/";

            let params = [("itemcount", "1"), ("publishedfileids[0]", workshopid)];

            self.post(url).form(&params)
        }

        pub fn request_many_published_file_details<'a, I>(
            &self,
            workshopids: I,
        ) -> reqwest::RequestBuilder
        where
            I: Iterator<Item = &'a str>,
        {
            let url =
                "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/";

            #[rustfmt::skip]
            const PUBLISHEDFILEIDSKEYS: [&str; 100] = ["publishedfileids[0]", "publishedfileids[1]", "publishedfileids[2]", "publishedfileids[3]", "publishedfileids[4]", "publishedfileids[5]", "publishedfileids[6]", "publishedfileids[7]", "publishedfileids[8]", "publishedfileids[9]", "publishedfileids[10]", "publishedfileids[11]", "publishedfileids[12]", "publishedfileids[13]", "publishedfileids[14]", "publishedfileids[15]", "publishedfileids[16]", "publishedfileids[17]", "publishedfileids[18]", "publishedfileids[19]", "publishedfileids[20]", "publishedfileids[21]", "publishedfileids[22]", "publishedfileids[23]", "publishedfileids[24]", "publishedfileids[25]", "publishedfileids[26]", "publishedfileids[27]", "publishedfileids[28]", "publishedfileids[29]", "publishedfileids[30]", "publishedfileids[31]", "publishedfileids[32]", "publishedfileids[33]", "publishedfileids[34]", "publishedfileids[35]", "publishedfileids[36]", "publishedfileids[37]", "publishedfileids[38]", "publishedfileids[39]", "publishedfileids[40]", "publishedfileids[41]", "publishedfileids[42]", "publishedfileids[43]", "publishedfileids[44]", "publishedfileids[45]", "publishedfileids[46]", "publishedfileids[47]", "publishedfileids[48]", "publishedfileids[49]", "publishedfileids[50]", "publishedfileids[51]", "publishedfileids[52]", "publishedfileids[53]", "publishedfileids[54]", "publishedfileids[55]", "publishedfileids[56]", "publishedfileids[57]", "publishedfileids[58]", "publishedfileids[59]", "publishedfileids[60]", "publishedfileids[61]", "publishedfileids[62]", "publishedfileids[63]", "publishedfileids[64]", "publishedfileids[65]", "publishedfileids[66]", "publishedfileids[67]", "publishedfileids[68]", "publishedfileids[69]", "publishedfileids[70]", "publishedfileids[71]", "publishedfileids[72]", "publishedfileids[73]", "publishedfileids[74]", "publishedfileids[75]", "publishedfileids[76]", "publishedfileids[77]", "publishedfileids[78]", "publishedfileids[79]", "publishedfileids[80]", "publishedfileids[81]", "publishedfileids[82]", "publishedfileids[83]", "publishedfileids[84]", "publishedfileids[85]", "publishedfileids[86]", "publishedfileids[87]", "publishedfileids[88]", "publishedfileids[89]", "publishedfileids[90]", "publishedfileids[91]", "publishedfileids[92]", "publishedfileids[93]", "publishedfileids[94]", "publishedfileids[95]", "publishedfileids[96]", "publishedfileids[97]", "publishedfileids[98]", "publishedfileids[99]"];

            let mut params: Vec<(&str, &str)> = PUBLISHEDFILEIDSKEYS
                .iter()
                .copied()
                .zip(workshopids)
                .collect();

            let itemcount = params.len().to_string();
            params.push(("itemcount", &itemcount));

            self.post(url).form(&params)
        }

        pub fn request_collection_details(&self, workshopid: &str) -> reqwest::RequestBuilder {
            let url = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/";

            let params = [
                ("collectioncount", "1"),
                ("publishedfileids[0]", workshopid),
            ];

            self.post(url).form(&params)
        }

        pub async fn collection_details(&self, workshopid: &str) -> anyhow::Result<Parts> {
            let req = self.request_collection_details(&workshopid);
            self.request_to_parts(req).await
        }
    }

    use axum::body::Bytes;
    use reqwest::StatusCode;

    pub struct Parts {
        pub status: StatusCode,
        pub body: Bytes,
    }

    impl Parts {
        pub fn json<'a, T>(&'a self) -> anyhow::Result<T>
        where
            T: Deserialize<'a>,
        {
            use anyhow::Context;

            serde_json::from_slice(&self.body)
                .context("parse json")
                .context(self.display())
        }

        pub fn display(&self) -> impl std::fmt::Display {
            display_response(self.status, &self.body)
        }
    }

    pub mod traits {}

    /* bytes::Bytes is cheaply clonable apparently */
    fn display_response<B>(status: reqwest::StatusCode, buf: &B) -> InternalResponseDisplay<B>
    where
        B: std::ops::Deref<Target = [u8]> + Clone,
    {
        InternalResponseDisplay {
            status,
            buf: buf.clone(),
        }
    }

    #[derive(Debug)]
    struct InternalResponseDisplay<B> {
        status: reqwest::StatusCode,
        buf: B,
    }

    impl<B> std::fmt::Display for InternalResponseDisplay<B>
    where
        B: std::ops::Deref<Target = [u8]>,
    {
        fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
            write!(f, "{} ~", self.status)?;
            match str::from_utf8(self.buf.as_ref()) {
                Ok(string) => write!(f, " {}", string),
                Err(_) => self.buf.iter().map(|i| write!(f, " {}", i)).collect(),
            }
        }
    }
}

#[derive(Debug)]
pub enum SystemMsg {
    QuitLevel(QuitLevel),
}

impl From<QuitLevel> for SystemMsg {
    fn from(v: QuitLevel) -> Self {
        Self::QuitLevel(v)
    }
}

#[derive(Debug)]
pub enum SteamCmdMsg {}

#[derive(Debug)]
pub enum SteamApiMsg {}

#[derive(Debug)]
pub enum ApiMsg {}

async fn async_main() -> Result<()> {
    let config = Config::default();

    let mut quitlevel: Option<QuitLevel> = None;

    let (mut sys_s, mut sys_r) = async_channel::bounded::<SystemMsg>(8);
    let (mut www_s, mut www_r) = async_channel::bounded::<ApiMsg>(8);

    #[derive(Debug)]
    enum ServiceResult {
        Www(std::io::Result<()>),
    }

    let mut stuff = tokio::task::JoinSet::<ServiceResult>::new();

    let listener = tokio::net::TcpListener::bind(&config.www)
        .await
        .context("bind to listen address")?;
    let www = www::Server { listener };

    info!("listening"; "addr" => www.listener.local_addr().as_ref().unwrap_or(&config.www));

    stuff.spawn({
        let sys_r = sys_r.clone();
        async move { ServiceResult::Www(www.run_forever(sys_r, www_s).await) }
    });

    let (mut quitstart_s, mut quitstart_r) = async_channel::bounded::<QuitLevel>(8);
    let (mut quitnext_s, mut quitnext_r) = async_channel::bounded::<QuitLevel>(8);
    let mut quittimer = tokio::spawn(quitlevel_timer(quitstart_r, quitnext_s));

    // let db_thread = std::thread::Builder::new()
    //     .name("materialist-db".to_string())
    //     .spawn(move || db::blocking_actor(db, db_rx))
    //     .context("start database thread")?;

    loop {
        tokio::select! {
            biased;

            res = stuff.join_next() => {
                let Some(res) = res else {
                    info!("clean shutdown");
                    break;
                };

                match res {
                    Ok(ServiceResult::Www(Ok(()))) => (),
                    Ok(ServiceResult::Www(Err(err))) => crit!("service failed"; "err" => err),
                    Err(joinerr) => crit!("panic?"; "err" => joinerr),
                }

                if quitlevel.is_none() {
                    /* if something quit but we aren't shutting down,
                     * then start shutting down */
                    let quitnext = QuitLevel::PoliteQuit;
                    quitstart_s.send(quitnext).await?;
                    sys_s.send(quitnext.into()).await?;
                    quitlevel = Some(quitnext);
                }
            }

            quitnext = quitnext_r.recv() => {
                let Ok(quitnext) = quitnext else {
                    crit!("shutting down"; "why" => "quit timer channel closed");
                    break;
                };

                quitlevel = Some(quitnext);
                quitstart_s.send(quitnext).await?;
                sys_s.send(quitnext.into()).await?;
            }

            quitnext = &mut quittimer => {
                crit!("shutting down"; "why" => "quit timer terminated");
                break;
            }

            sig = tokio::signal::ctrl_c() => {
                if let Err(err) = sig {
                    /* TODO */
                    crit!("shutting down"; "why" => "ctrl_c signal handler failed");
                    break;
                }

                let quitnext = quitlevel.map(QuitLevel::next).unwrap_or_default();

                if quitlevel.replace(quitnext) == Some(QuitLevel::Kill) {
                    warn!("quitting for real this time");
                    break;
                }

                warn!("ctrl-c, quitting soon"; "level" => logging::dbg(quitnext));

                sys_s.send(quitnext.into()).await?;
            }
        };
    }

    return Ok(());

    async fn quitlevel_timer(
        r: async_channel::Receiver<QuitLevel>,
        s: async_channel::Sender<QuitLevel>,
    ) {
        use tokio::time::{Duration, sleep};

        while let Ok(quitlevel) = r.recv().await {
            sleep(match quitlevel {
                QuitLevel::PoliteQuit => Duration::from_secs(10),
                QuitLevel::UnpoliteQuit => Duration::from_secs(5),
                QuitLevel::Kill => Duration::from_secs(5),
            })
            .await;

            if s.send(quitlevel.next()).await.is_err() {
                break;
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
struct Config {
    pub podman: String,
    pub podman_pull_args: Vec<String>,
    pub steamcmd_image: String,
    pub www: std::net::SocketAddr,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            podman: "podman".to_owned(),
            podman_pull_args: vec!["pull".to_owned()],
            steamcmd_image: "docker.io/steamcmd/steamcmd:latest".to_owned(),
            www: "127.0.0.1:8847".parse().unwrap(),
        }
    }
}

pub mod www {
    /* axum wants to be Send even if I'm only using it in a single thread lmao */
    use axum::{
        Router,
        extract::{Extension, Path},
        http::{Request, StatusCode, header},
        response::{Html, IntoResponse, Response},
    };

    use facet::Facet;
    use std::sync::Arc;

    use super::steamapi::{self, traits::*};
    use crate::crit;

    #[derive(Debug)]
    pub struct Server {
        pub listener: tokio::net::TcpListener,
    }

    impl Server {
        // pub async fn new(address: &str) -> std::io::Result<Self> {
        //     let listener = tokio::net::TcpListener::bind(address).await?;
        //     Ok(Self { listener })
        // }

        pub async fn run_forever(
            self,
            sys_r: async_channel::Receiver<super::SystemMsg>,
            _: async_channel::Sender<super::ApiMsg>,
        ) -> std::io::Result<()> {
            use axum::routing::{Router, get, post};

            let app = Router::new()
                .route("/", get(root))
                // .route("/workshop/:key/", get(get_workshop_object))
                // .route("/workshop-item/{key}/", get(get_workshop_item))
                .route("/workshop-item/{key}/", post(refresh_workshop_item))
                .route("/workshop-collection/{key}/", post(refresh_workshop_collection))
                // .route("/idk", post(schedule_something))
                .layer(Extension(Arc::new(steamapi::Client::new())))
                // .layer(axum::middleware::from_fn(wow))
                ;

            axum::serve(self.listener, app)
                .with_graceful_shutdown(async move { drop(sys_r.recv().await) })
                .await?;

            Ok(())
        }
    }

    async fn root() -> &'static str {
        "Hello, World!"
    }

    async fn refresh_workshop_item(
        Extension(steamapi): Extension<Arc<steamapi::Client>>,
        Path(workshopid): Path<String>,
    ) -> axum::response::Result<String> {
        use anyhow::Context;

        let req = steamapi.request_published_file_details(&workshopid);

        let parts = steamapi
            .request_to_parts(req)
            .await
            .context("published_file_details")
            .map_err(internal_error)?;

        let r = parts
            .json::<steamapi::PublishedFileDetailsResponse>()
            .context("published_file_details")
            .map_err(internal_error)?;

        dbg!(&r);

        Ok("OK".to_string())
    }

    async fn refresh_workshop_collection(
        Extension(steamapi): Extension<Arc<steamapi::Client>>,
        Path(workshopid): Path<String>,
    ) -> axum::response::Result<String> {
        use anyhow::Context;

        let parts = steamapi
            .collection_details(&workshopid)
            .await
            .context("request_collection_details")
            .map_err(internal_error)?;

        let r = parts
            .json::<steamapi::CollectionDetailsResponse>()
            .context("request_collection_details")
            .map_err(internal_error)?;

        // match r.response.collectiondetails.get(0) {
        //     Some(id) if id == workshopid {
        //     }
        // }
        // if !matches!(Some(workshopid), 

        let Some(details) = r.response.collectiondetails.iter().find(|d| d.publishedfileid == workshopid) else {
            return Err(anyhow::anyhow!("requested workshopid expected in collectiondetails"))
                .context(parts.display())
                .map_err(internal_error);
        };

        dbg!(r);

        Ok("WOOT".to_string())
    }

    pub fn not_found<E>(err: E) -> Response
    where
        anyhow::Error: From<E>,
    {
        crit!("not found"; "err" => crate::logging::err(err));
        (StatusCode::NOT_FOUND, "not found").into_response()
    }

    pub fn internal_error<E>(err: E) -> Response
    where
        anyhow::Error: From<E>,
    {
        crit!("internal error"; "err" => crate::logging::err(err));
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            "something went wrong with the computer doing the website and the thing didn't work, sorry",
        )
            .into_response()
    }

    pub fn too_many_requests<E>(err: E) -> Response
    where
        anyhow::Error: From<E>,
    {
        crit!("too many requests"; "err" => crate::logging::err(err));
        (
            StatusCode::TOO_MANY_REQUESTS,
            "this computer too busy (x . x) ~~zzZ",
        )
            .into_response()
    }

    pub fn bad_request<E>(err: E) -> Response
    where
        E: std::fmt::Display,
        anyhow::Error: From<E>,
    {
        let body = format!("{}", &err);
        crit!("bad request"; "err" => crate::logging::err(err));
        (StatusCode::BAD_REQUEST, body).into_response()
    }
}


pub mod db {
    use rusqlite::Connection;

    pub enum Query {
        Test,
    }

    pub struct Db {
        conn: Connection,
        inbox: async_channel::Receiver<()>,
    }

    pub fn connect<P>(path: P) -> rusqlite::Result<Connection>
    where
        P: AsRef<std::path::Path>,
    {
        use rusqlite::{OpenFlags, ToSql, types::Value};

        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            // The new database connection will use the multi-thread threading mode.
            // Multi-thread. In this mode, SQLite can be safely used by multiple threads provided that
            // no single database connection is used simultaneously in two or more threads.
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            // FYI this will share the underlying file handle.  So opening a new connection will not
            // actually open a new file with the operating system if an existing connection is open to
            // the same path.
            | OpenFlags::SQLITE_OPEN_SHARED_CACHE;

        let conn = Connection::open_with_flags(path, flags)?;

        conn.pragma_update_and_check(None, "foreign_keys", &1 as &dyn ToSql, |row| {
            row.get::<_, Value>(0)
        })?;

        Ok(conn)
    }

    impl Db {
        fn run_blocking(&mut self) -> anyhow::Result<()> {
            while let Ok(msg) = self.inbox.recv_blocking() {
                let tx = self.conn.transaction()?;

                let datetime: String = tx
                    .prepare_cached(
                        r#"
                    SELECT datetime()
                        "#,
                    )?
                    .query_one([], |r| r.get(0))?;

                dbg!(datetime);
            }
            Ok(())
        }
    }

    // impl super::Serviette for Db {
    // }
}

#[derive(Debug)]
struct Command {
    cmd: tokio::process::Command,
    quitlevel: async_channel::Receiver<QuitLevel>,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, PartialOrd)]
pub enum QuitLevel {
    #[default]
    PoliteQuit,
    UnpoliteQuit,
    Kill,
}

impl QuitLevel {
    pub fn next(self) -> Self {
        match self {
            Self::PoliteQuit => Self::UnpoliteQuit,
            _ => Self::Kill,
        }
    }
}

// let wow = tokio::process::Command::new("podman")
//     .arg("pull")
//     .arg("docker.io/steamcmd/steamcmd:latest");

// let mut cmd = tokio::process::Command::new("fish");
// cmd.arg("-c")
//     .arg("for i in (seq 9); echo foo $i >&2; sleep .1; end; and echo woot");

// run_cmd(Command { cmd, quitlevel }).await?;

async fn run_cmd(cmd: Command) -> Result<()> {
    use std::os::fd::AsRawFd;
    use std::time::Instant;
    use tokio::io::AsyncReadExt;

    let Command {
        mut cmd,
        mut quitlevel,
    } = cmd;

    /* pipe child stdout back to us */

    let (rx, tx) = std::io::pipe()?;

    let mut rx = tokio::net::unix::pipe::Receiver::from_owned_fd(rx.into())?
        /* read at most, this much */
        .take(256_1024_1024);

    let then = Instant::now();

    let mut child = cmd
        .stdin(std::process::Stdio::null())
        .stdout(tx.try_clone()?)
        .stderr(tx)
        .spawn()?;

    /* this drops tx in our process, ensures that when the child exits, rx will EOF */
    drop(cmd);

    let mut read_stdout = tokio::task::spawn(async {
        /* it's important to drop rx at the end of this scope because, if it isn't already closed,
         * we must close our end to notify the writer that we have no further intention to read */
        let mut rx = rx;
        let mut buf = Vec::<u8>::new();
        let res = rx.read_to_end(&mut buf).await;
        (buf, res)
    });

    let exit = loop {
        tokio::select! {
            biased;

            exit = child.wait() => { break exit }

            level = quitlevel.recv() => {
                use rustix::process::Signal;

                let signal = match level {
                    Ok(QuitLevel::PoliteQuit) => Signal::INT,
                    Ok(QuitLevel::UnpoliteQuit) => Signal::TERM,
                    Ok(QuitLevel::Kill) => Signal::KILL,
                    Err(_) => Signal::KILL,
                };

                if let Some(pid) = child.pid() {
                    dbg!((pid, signal));
                    let _ = rustix::process::kill_process(pid, signal);
                }
            }
        };
    };

    dbg!(&exit);

    let (stdout, stdout_res) = read_stdout.await?;

    dbg!(String::from_utf8_lossy(&stdout));
    let _todo = dbg!(stdout_res);

    let duration = Instant::now().saturating_duration_since(then);

    dbg!(duration);

    Ok(())
}

use traits::*;

pub mod traits {
    use rustix::process::{Pid, Signal};

    pub trait ChildExt {
        fn pid(&self) -> Option<Pid>;
    }

    impl ChildExt for tokio::process::Child {
        fn pid(&self) -> Option<Pid> {
            self.id()
                .and_then(|pid| i32::try_from(pid).ok())
                .and_then(Pid::from_raw)
        }
    }

    //     trait PidExt {
    //         fn send_signal(self, _: Signal) -> rustix::io::Result<()>;
    //     }

    //     impl PidExt for Pid {
    //         fn send_signal(self, signal: Signal) -> rustix::io::Result<()> {
    //             rustix::process::kill_process(self, signal)
    //         }
    //     }
}
