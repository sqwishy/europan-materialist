#![allow(unused_variables)]
#![allow(unused_imports)]
#![allow(unused_mut)]
#![allow(dead_code)]

use anyhow::{Context, Result};
use std::sync::Arc;

fn main() -> Result<()> {
    // let (sig_tx, sig_rx) = async_channel::bounded::<()>(8);

    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()?;

    println!("Hello, world!");

    dbg!(new_session_string());

    // rt.block_on(async_main()).context("async_main")?;

    Ok(())
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

mod steamapi {
    use facet::Facet;

    #[derive(Facet, Debug)]
    pub struct Response<T> {
        pub response: T,
    }

    #[derive(Facet, Debug)]
    pub struct PublishedFileDetailsResults<'a> {
        pub publishedfiledetails: Vec<PublishedFileDetails<'a>>,
    }

    #[derive(Facet, Debug)]
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

    pub type PublishedFileDetailsResponse<'a> = Response<PublishedFileDetailsResults<'a>>;

    #[derive(Facet, Debug)]
    pub struct GetPlayerSummariesResults<'a> {
        pub players: Vec<PlayerSummary<'a>>,
    }

    #[derive(Facet, Debug)]
    pub struct PlayerSummary<'a> {
        pub steamid: &'a str,
        pub personaname: &'a str,
        pub profileurl: &'a str,
        pub avatar: &'a str,
        pub avatarmedium: &'a str,
        pub avatarmediumfull: &'a str,
    }

    pub type GetPlayerSummariesResponse<'a> = Response<GetPlayerSummariesResults<'a>>;
}

#[derive(Debug)]
enum SystemMsg {
    RunLevel(RunLevel),
}

async fn async_main() -> Result<()> {
    let config = Config::default();

    let mut runlevel = RunLevel::Running;

    let (mut tx, mut rx) = tokio::sync::watch::channel(runlevel);

    let mut set = tokio::task::JoinSet::<()>::new();

    let mut api = tokio::task::spawn(
        www::ApiServer::new(&config.api)
            .await
            .context("init api server")?
            .serve_forever(),
    );

    // let db_thread = std::thread::Builder::new()
    //     .name("materialist-db".to_string())
    //     .spawn(move || db::blocking_actor(db, db_rx))
    //     .context("start database thread")?;

    while !(api.is_finished() && set.is_empty()) {
        tokio::select! {
            biased;

            /* The task set can be empty under zero load. Don't poll on it in that case as it will
             * return None immediately.
             *
             * Only poll on an empty TaskSet when we are shutting down and want to know that it's
             * empty I guess? */
            task = set.join_next(), if !set.is_empty() => {
                let Some(task) = task else {
                    continue;
                };
                let _todo = dbg!(task);
            }

            sig = tokio::signal::ctrl_c() => {
                runlevel = sig.map(|()| runlevel.next())
                    .unwrap_or(RunLevel::Kill);
                if tx.send_replace(runlevel) == RunLevel::Kill && runlevel == RunLevel::Kill {
                    dbg!("super kill");
                    break;
                }
            }

            res = &mut api, if !api.is_finished() => {
                dbg!("long running api stopped!");
                dbg!(&res);
                /* TODO advance on timer */
                if runlevel == RunLevel::Running {
                    runlevel = RunLevel::PoliteQuit;
                    tx.send_replace(runlevel);
                }
            }
        };
    }

    // let url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/";
    // let params = [
    //     ("itemcount", "1"), /**/
    //     ("publishedfileids[0]", "3045796581c"),
    // ];
    // let client = reqwest::Client::new();
    // let body = client.post(url).form(&params).send().await?.text().await?;

    // dbg!(&body);

    // let body = include_str!("/tmp/derp.json");
    // dbg!(facet_json::from_str::<steamapi::PublishedFileDetailsResponse>(&body));

    // podman_pull_steamcmd().await?;

    Ok(())
}

#[derive(Debug, Clone, PartialEq)]
struct Config {
    pub podman: String,
    pub podman_pull_args: Vec<String>,
    pub steamcmd_image: String,
    pub api: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            podman: "podman".to_owned(),
            podman_pull_args: vec!["pull".to_owned()],
            steamcmd_image: "docker.io/steamcmd/steamcmd:latest".to_owned(),
            api: "127.0.0.1:8847".to_owned(),
        }
    }
}

pub mod www {
    /* axum wants to be Send even if I'm only using it in a single thread lmao */
    use std::sync::Arc;
    use axum::extract::State;
    use facet::Facet;

    #[derive(Debug)]
    pub struct ApiServer {
        pub listener: tokio::net::TcpListener,
    }

    pub struct ApiConfig;

    impl ApiServer {
        pub async fn new(address: &str) -> std::io::Result<Self> {
            let listener = tokio::net::TcpListener::bind(address).await?;
            Ok(Self { listener })
        }

        pub async fn serve_forever(self) -> std::io::Result<()> {
            use axum::routing::{Router, get, post};

            let app = Router::new()
                .route("/", get(root))
                // .route("/idk", post(schedule_something))
                .with_state(Arc::new(ApiConfig))
                ;

            axum::serve(self.listener, app)
                // .with_graceful_shutdown(todo!())
                .await?;

            Ok(())
        }
    }

    async fn root(_: State<Arc<ApiConfig>>) -> &'static str {
        "Hello, World!"
    }

    #[derive(Facet, Debug)]
    pub struct Response<T> {
        pub response: T,
    }
}

trait Serviette {}

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
    runlevel: tokio::sync::watch::Receiver<RunLevel>,
}

#[derive(Debug, Clone, Copy, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum RunLevel {
    Running = 0,
    PoliteQuit,
    UnpoliteQuit,
    Kill,
}

impl RunLevel {
    pub fn next(self) -> Self {
        match self {
            Self::Running => Self::PoliteQuit,
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

// run_cmd(Command { cmd, runlevel }).await?;

async fn run_cmd(cmd: Command) -> Result<()> {
    use std::os::fd::AsRawFd;
    use std::time::Instant;
    use tokio::io::AsyncReadExt;

    let Command {
        mut cmd,
        mut runlevel,
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

            run = runlevel.changed() => {
                use rustix::process::Signal;

                let signal = match run {
                    Ok(()) => match runlevel.borrow_and_update().clone() {
                        RunLevel::Running => continue,
                        RunLevel::PoliteQuit => Signal::INT,
                        RunLevel::UnpoliteQuit => Signal::TERM,
                        RunLevel::Kill => Signal::KILL,
                    }
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
