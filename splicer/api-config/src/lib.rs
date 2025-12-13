use std::net::SocketAddr;
use std::time::Duration;

use http::header::{HeaderName, HeaderValue};
use url::Url;

use miette::SourceSpan;

use kdl::{KdlDocument, KdlError, KdlNode, KdlValue};

pub type Result<T, E = ConfigError> = std::result::Result<T, E>;

pub fn decode(src: &str) -> Result<Config> {
    let fails = Default::default();
    Decode { src, fails }.run()
}

#[derive(Debug, miette::Diagnostic, thiserror::Error)]
pub enum ConfigError {
    #[error("parse")]
    #[diagnostic(transparent)]
    Parse(#[source] KdlError),

    #[error("structural error(s)")]
    Structural {
        #[source_code]
        src: String,
        #[related]
        errors: Vec<Error>,
    },
}

#[derive(Debug, miette::Diagnostic, thiserror::Error)]
pub enum Error {
    #[error("expected {thing}")]
    // #[diagnostic(code(unexpected-node))]
    Expected {
        thing: &'static str,
        #[label]
        at: SourceSpan,
    },

    #[error("unexpected node")]
    Unexpected {
        #[label]
        at: SourceSpan,
    },
}

#[derive(Debug, PartialEq)]
pub struct Config {
    pub db: String,
    // pub afk_timer: Duration,
    pub www: WwwConfig,
    pub steamcmd: HttpReqConfig,
    pub steamcommunity: HttpReqConfig,
    pub podman: HttpReqConfig,
    pub publish: PublishConfig,
    pub build: BuildConfig,
}

#[derive(Debug, PartialEq)]
pub struct WwwConfig {
    pub listen: SocketAddr,
    pub debug_auth: String,
    pub wait_on_publish_poll_interval: Duration,
    pub response_headers: Vec<(HeaderName, HeaderValue)>,
}

#[derive(Debug, PartialEq)]
pub struct HttpReqConfig {
    pub url: Url,
    pub unix: String,
    pub concurrency: u8,
    pub user_agent: String,
    pub read_timeout: Duration,
}

#[derive(Debug, PartialEq)]
pub struct BuildConfig {
    pub image: String,
    pub work_inner: String,
    pub work_outer: String,
    pub vanilla: String,
}

#[derive(Debug, PartialEq)]
pub struct PublishConfig {
    pub image: String,
    pub work_inner: String,
    pub work_outer: String,
    pub secrets_volume: String,
    pub deploy_site: String,
}

impl Config {
    pub fn inner_work_dir_for_build(&self, pk: i64) -> String {
        format!("{}/{}-build", self.build.work_inner, pk)
    }
    pub fn outer_work_dir_for_build(&self, pk: i64) -> String {
        format!("{}/{}-build", self.build.work_outer, pk)
    }
    pub fn inner_work_dir_for_publish(&self, pk: i64) -> String {
        format!("{}/{}-publish", self.publish.work_inner, pk)
    }
    pub fn outer_work_dir_for_publish(&self, pk: i64) -> String {
        format!("{}/{}-publish", self.publish.work_outer, pk)
    }
}

#[derive(Debug)]
struct Decode<'s> {
    src: &'s str,
    fails: Vec<Error>,
}

impl<'s> Decode<'s> {
    fn run(&mut self) -> Result<Config, ConfigError> {
        let doc = self
            .src
            .parse::<KdlDocument>()
            .map_err(ConfigError::Parse)?;

        let mut cfg = Config::default();

        for node in doc.nodes() {
            match node.name().value() {
                "db" => self.expect_string(&mut cfg.db, node),
                // "afk-timer-sec" => self.expect_duration_sec(&mut cfg.afk_timer, node),
                "www" => self.expect_www(&mut cfg.www, node),
                "steamcommunity" => self.expect_httpreq(&mut cfg.steamcommunity, node),
                "steamcmd" => self.expect_httpreq(&mut cfg.steamcmd, node),
                "podman" => self.expect_httpreq(&mut cfg.podman, node),
                "build" => self.expect_build(&mut cfg.build, node),
                "publish" => self.expect_publish(&mut cfg.publish, node),
                _ => self.unexpected(node),
            };
        }

        if !self.fails.is_empty() {
            Err(ConfigError::Structural {
                src: self.src.to_string(),
                errors: std::mem::take(&mut self.fails),
            })?
        }

        Ok(cfg)
    }

    fn expected(&mut self, thing: &'static str, node: &KdlNode) {
        let at = node.span();
        self.fails.push(Error::Expected { thing, at });
    }

    fn unexpected(&mut self, node: &KdlNode) {
        let at = node.span();
        self.fails.push(Error::Unexpected { at });
    }

    fn expect_string(&mut self, v: &mut String, node: &KdlNode) {
        match single_value(node).and_then(|v| v.as_string()) {
            Some(s) => *v = s.to_string(),
            None => self.expected("single string", node),
        }
    }

    fn expect_u8(&mut self, v: &mut u8, node: &KdlNode) {
        match single_value(node)
            .and_then(|v| v.as_integer())
            .and_then(|i| u8::try_from(i).ok())
        {
            Some(i) => *v = i,
            None => self.expected("single number in range 0..255", node),
        }
    }

    fn expect_url(&mut self, v: &mut Url, node: &KdlNode) {
        match single_value(node)
            .and_then(|v| v.as_string())
            .and_then(|s| s.parse().ok())
        {
            Some(u) => *v = u,
            None => self.expected("url", node),
        }
    }

    // fn expect_url_opt(&mut self, v: &mut Option<Url>, node: &KdlNode) {
    //     match single_value(node) {
    //         Some(KdlValue::String(s)) => match s.parse().ok() {
    //             Some(u) => *v = Some(u),
    //             _ => self.expected("url or null", node),
    //         },
    //         Some(KdlValue::Null) => *v = None,
    //         _ => self.expected("url or null", node),
    //     }
    // }

    fn expect_sockaddr(&mut self, v: &mut SocketAddr, node: &KdlNode) {
        match single_value(node)
            .and_then(|v| v.as_string())
            .and_then(|s| s.parse().ok())
        {
            Some(a) => *v = a,
            None => self.expected("socket address (hostname-or-ip:port)", node),
        }
    }

    fn expect_duration_ms(&mut self, v: &mut Duration, node: &KdlNode) {
        match single_value(node)
            .and_then(|v| v.as_integer())
            .and_then(|i| u64::try_from(i).ok())
        {
            Some(i) => *v = Duration::from_millis(i),
            None => self.expected("single number in range 0..18,446 quadrillion", node),
        }
    }

    // fn expect_duration_sec(&mut self, v: &mut Duration, node: &KdlNode) {
    //     match single_value(node)
    //         .and_then(|v| v.as_integer())
    //         .and_then(|i| u64::try_from(i).ok())
    //     {
    //         Some(i) => *v = Duration::from_secs(i),
    //         None => self.expected("single number in range 0..18,446 quadrillion", node),
    //     }
    // }

    fn expect_www(&mut self, v: &mut WwwConfig, node: &KdlNode) {
        let Some(doc) = node.children() else {
            self.expected("children", node);
            return;
        };

        for node in doc.nodes() {
            match node.name().value() {
                "listen" => self.expect_sockaddr(&mut v.listen, node),
                "debug-auth" => self.expect_string(&mut v.debug_auth, node),
                "response-headers" => self.expect_headers(&mut v.response_headers, node),
                "wait-on-publish-poll-interval-ms" => {
                    self.expect_duration_ms(&mut v.wait_on_publish_poll_interval, node)
                }
                _ => self.unexpected(node),
            }
        }
    }

    fn expect_httpreq(&mut self, v: &mut HttpReqConfig, node: &KdlNode) {
        let Some(doc) = node.children() else {
            self.expected("children", node);
            return;
        };

        for node in doc.nodes() {
            match node.name().value() {
                "url" => self.expect_url(&mut v.url, node),
                "unix" => self.expect_string(&mut v.unix, node),
                "concurrency" => self.expect_u8(&mut v.concurrency, node),
                "user-agent" => self.expect_string(&mut v.user_agent, node),
                "read-timeout-ms" => self.expect_duration_ms(&mut v.read_timeout, node),
                _ => self.unexpected(node),
            }
        }
    }

    fn expect_headers(&mut self, v: &mut Vec<(HeaderName, HeaderValue)>, node: &KdlNode) {
        let Some(doc) = node.children() else {
            self.expected("children", node);
            return;
        };

        v.clear();

        for node in doc.nodes() {
            let Ok(name) = node.name().value().parse() else {
                self.expected("header name", node);
                continue;
            };

            let Some(value) = single_value(node)
                .and_then(|v| v.as_string())
                .and_then(|s| s.parse().ok())
            else {
                self.expected("single string", node);
                continue;
            };

            v.push((name, value));
        }
    }

    fn expect_build(&mut self, v: &mut BuildConfig, node: &KdlNode) {
        let Some(doc) = node.children() else {
            self.expected("children", node);
            return;
        };

        for node in doc.nodes() {
            match node.name().value() {
                "image" => self.expect_string(&mut v.image, node),
                "work-inner" => self.expect_string(&mut v.work_inner, node),
                "work-outer" => self.expect_string(&mut v.work_outer, node),
                "vanilla" => self.expect_string(&mut v.vanilla, node),
                _ => self.unexpected(node),
            }
        }
    }

    fn expect_publish(&mut self, v: &mut PublishConfig, node: &KdlNode) {
        let Some(doc) = node.children() else {
            self.expected("children", node);
            return;
        };

        for node in doc.nodes() {
            match node.name().value() {
                "image" => self.expect_string(&mut v.image, node),
                "work-inner" => self.expect_string(&mut v.work_inner, node),
                "work-outer" => self.expect_string(&mut v.work_outer, node),
                "secrets-volume" => self.expect_string(&mut v.secrets_volume, node),
                "deploy-site" => self.expect_string(&mut v.deploy_site, node),
                _ => self.unexpected(node),
            }
        }
    }
}

fn single_value(node: &KdlNode) -> Option<&KdlValue> {
    match node.entries() {
        [entry] if entry.name().is_none() && node.children().is_none() => Some(entry.value()),
        _ => None,
    }
}

impl Default for Config {
    fn default() -> Config {
        Self {
            db: "/tmp/materialist-rs.sqlite".to_string(),
            www: WwwConfig {
                listen: "127.0.0.1:8847".parse().unwrap(),
                debug_auth: "".to_string(),
                wait_on_publish_poll_interval: Duration::from_millis(500),
                response_headers: vec![
                    (
                        HeaderName::from_static("access-control-allow-origin"),
                        HeaderValue::from_static("*"),
                    ),
                    (
                        HeaderName::from_static("access-control-allow-methods"),
                        HeaderValue::from_static("POST,GET,OPTIONS"),
                    ),
                ],
            },
            steamcmd: HttpReqConfig {
                url: "http://localhost:8888/".parse::<Url>().unwrap().into(),
                unix: Default::default(),
                concurrency: 3,
                user_agent: "europan-materialist/0 (materialist.pages.dev)".to_string(),
                read_timeout: Default::default(),
            },
            steamcommunity: HttpReqConfig {
                url: "https://steamcommunity.com/".parse::<Url>().unwrap().into(),
                unix: Default::default(),
                concurrency: 4,
                user_agent: "europan-materialist/0 (materialist.pages.dev)".to_string(),
                read_timeout: Duration::from_secs(30).into(),
            },
            podman: HttpReqConfig {
                url: "http://p/".parse::<Url>().unwrap().into(),
                unix: "/run/user/1000/podman/podman.sock".parse().unwrap(),
                concurrency: 8,
                user_agent: "europan-materialist/0 (materialist.pages.dev)".to_string(),
                read_timeout: Default::default(),
            },
            build: BuildConfig {
                image: "splicer-build".to_string(),
                work_inner: "/tmp/spl-api-work".to_string(),
                work_outer: "/tmp/spl-api-work".to_string(),
                vanilla: "barotrauma".to_string(),
            },
            publish: PublishConfig {
                image: "splicer-publish".to_string(),
                work_inner: "/tmp/spl-api-work".to_string(),
                work_outer: "/tmp/spl-api-work".to_string(),
                secrets_volume: "materialist-secrets".to_string(),
                deploy_site: "materialist-next".to_string(),
            },
        }
    }
}

pub fn default_document() -> KdlDocument {
    // let mut doc = KdlDocument::new();
    DEFAULT_CONFIG.parse().unwrap()
}

pub const DEFAULT_CONFIG: &'static str = r#"
db "/tmp/materialist-rs.sqlite"

// NYI // shutdown after not handling a request after this long
// afk-timer-sec 0

www {
    // listen address, not used when activated by systemd socket activation
    listen "127.0.0.1:8847"

    // value of Authorization header for "private" URLs at /x/...
    // with an empty debug-auth, those routes will always return 404
    debug-auth ""

    wait-on-publish-poll-interval-ms 500

    // extra headers to add to the end of each response
    response-headers {
        access-control-allow-origin *
        access-control-allow-methods POST,GET,OPTIONS
    }

}

// access to public/unlisted workshop pages on steam community
steamcommunity {
    url "https://steamcommunity.com/"
    concurrency 4
    read-timeout-ms 30_000
    user-agent "europan-materialist/0 (materialist.pages.dev)"
}

// access to the steamcmd downloading service in this project
steamcmd {
    url "http://localhost:8888/"
    concurrency 3
    user-agent "europan-materialist/0 (materialist.pages.dev)"
}

podman {
    unix "/run/user/1000/podman/podman.sock"
    concurrency 8
    user-agent "europan-materialist/0 (materialist.pages.dev)"
}

build {
    image splicer-build

    // podman volume name to use as vanilla core package
    vanilla barotrauma

    // path in this container to temporary files to be shared with the build container
    // can be the same value as in the publish section
    work-inner "/tmp/spl-api-work"
    // path on host to work-inner; specified as a bind mount when creating build container
    work-outer "/tmp/spl-api-work"
}

publish {
    image splicer-publish

    // path in this container to temporary files to be shared with the publish container
    // can be the same value as in the build section
    work-inner "/tmp/spl-api-work"
    // path on host to work-inner; specified as a bind mount when creating publish container
    work-outer "/tmp/spl-api-work"

    // A volume containing a file named `cloudflare` that looks like;
    // CLOUDFLARE_ACCOUNT_ID=...
    // CLOUDFLARE_API_TOKEN=...
    secrets-volume materialist-secrets
    // value for PROJECT_NAME environment variable in publish container
    deploy-site materialist-next
}
"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_works() -> miette::Result<()> {
        let got = decode(DEFAULT_CONFIG)?;
        let expected = Config::default();
        assert_eq!(expected, got);
        Ok(())
    }

    #[test]
    fn errors() {
        let res = decode(
            &r#"
            db 123 // wrong type
            www {
                listen derp="spam" // entry should have no name
                response-headers {
                    foo "*"
                }
            }
            unexpected // not expected
            steamcmd // no children
            build {
                work-outer foo bar spam // too many nodes
                work-inner "/tmp" { } // shouldn't have children
            }
            "#,
        );

        let errors = match &res {
            Err(ConfigError::Structural { errors, .. }) => errors,
            _ => return assert!(false),
        };
        assert_eq!(errors.len(), 6);

        eprintln!("{:?}", res.map_err(miette::Report::from));
    }
}
