use std::io;
use std::path::{Path, PathBuf};

use anyhow::{Context, format_err};

use crate::ansi;

pub use toml::Value;

pub struct NoArgs<T> {
    path: PathBuf,
    t: std::marker::PhantomData<T>,
}

impl<T> NoArgs<T>
where
    for<'de> T: serde::de::Deserialize<'de>,
{
    pub fn path(&self) -> &Path {
        self.path.as_ref()
    }

    /// can resolve an absolute path to the file, may be useful in case the process changes
    /// directories and wants a more stable reference or something
    pub fn canonicalize(&mut self) -> io::Result<()> {
        self.path = self.path.canonicalize()?;
        Ok(())
    }

    pub fn read_and_parse(&self) -> anyhow::Result<T> {
        let s = std::fs::read_to_string(&self.path).with_context(|| {
            format_err!("read configuration at {}", self.path.display())
        })?;
        Self::parse(&s).with_context(|| {
            format_err!("parse configuration at {}", self.path.display())
        })
    }

    pub fn parse(s: &str) -> Result<T, toml::de::Error> {
        toml::from_str(s)
    }
}

pub fn from_argv<T, P: AsRef<Path>>(name: &str, default: P) -> NoArgs<T>
where
    for<'de> T: serde::Deserialize<'de> + serde::Serialize + Default,
{
    let mut path = default.as_ref().to_owned();
    let mut positional = vec![&mut path].into_iter();

    let mut allow_flags = true;
    let mut only_show = false;

    let mut args = std::env::args();
    let _exe = args.next();

    while let Some(arg) = args.next() {
        if allow_flags && arg.starts_with('-') {
            match arg.as_ref() {
                "-h" | "--help" => quit_with(usage(name, default)),
                "--show" => {
                    only_show = true;
                }
                "--show-default" => {
                    let s = toml::to_string_pretty(&T::default()).unwrap();
                    println!("{}", s);
                    std::process::exit(0);
                }
                "--" => allow_flags = false,
                _ => {
                    let objection = ansi::red.fg("unexpected option");
                    eprintln!("{} » {}", objection, arg);
                    quit_with(usage(name, default));
                }
            }
        } else {
            match positional.next() {
                None => {
                    let objection = ansi::red.fg("unexpected positional argument");
                    eprintln!("{} » {}", objection, arg);
                    quit_with(usage(name, default));
                }
                Some(p) => {
                    let _ = std::mem::replace(p, arg.into());
                }
            }
        }
    }

    let t = Default::default();
    let no = NoArgs { path, t };

    if only_show {
        match no.read_and_parse() {
            Ok(t) => {
                eprintln!("{} » {}", no.path.display(), ansi::blue.fg("success"));
                let s = toml::to_string_pretty(&t).unwrap();
                println!("{}", s);
                std::process::exit(0);
            }
            Err(oops) => {
                eprintln!(
                    "{} » {} {} {}",
                    no.path.display(),
                    ansi::red.fg("couldn't read your"),
                    ansi::red.fg(name),
                    ansi::red.fg("config"),
                );
                eprintln!("- {}", oops);
                let mut source = oops.source();
                while let Some(err) = source {
                    eprintln!("- {}", err);
                    source = err.source()
                }
                std::process::exit(2);
            }
        }
    } else {
        return no;
    }

    fn quit_with<S: AsRef<str>>(msg: S) -> ! {
        eprintln!("{}", msg.as_ref());
        std::process::exit(2);
    }

    fn usage<P: AsRef<Path>>(exe: &str, path: P) -> String {
        let path = path.as_ref().display();
        format!(
            "{} » {} [--show-default] (or) [--show] [{}]\n\
            \t--show        \tread the config and display it stdout and exit...\n\
            \t              \t...failing if the file does not exist or is busted\n\
            \t--show-default\tshow default config file to stdout and exit",
            ansi::blue.fg("usage"),
            exe,
            path
        )
    }
}

pub mod duration_ms {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::time::Duration;

    pub fn serialize<S>(t: &Duration, er: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        /* toml can't serialize u128 and we happen to
         * don't care about those durations anyway */
        (t.as_millis() as u64).serialize(er)
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Duration, D::Error>
    where
        D: Deserializer<'de>,
    {
        u64::deserialize(deserializer).map(Duration::from_millis)
    }
}

pub mod opt_duration_ms {
    use serde::{Deserialize, Deserializer, Serialize, Serializer, de, de::Visitor};
    use std::{fmt, time::Duration};

    pub fn serialize<S>(o: &Option<Duration>, er: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match o {
            Some(t) => (t.as_millis() as u64).serialize(er),
            None => Never.serialize(er),
        }
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Option<Duration>, D::Error>
    where
        D: Deserializer<'de>,
    {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Opt {
            Never(Never),
            This(u64),
        }

        match Opt::deserialize(deserializer)? {
            Opt::Never(_) => Ok(None),
            Opt::This(n) => Ok(Some(Duration::from_millis(n))),
        }
    }

    #[derive(PartialEq, Eq, Debug)]
    struct Never;

    impl Serialize for Never {
        fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
        where
            S: Serializer,
        {
            serializer.serialize_str("never")
        }
    }

    impl<'de> Deserialize<'de> for Never {
        fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
        where
            D: Deserializer<'de>,
        {
            let s = String::deserialize(deserializer)?;
            match s.as_ref() {
                "never" => Ok(Never),
                s => Err(de::Error::invalid_value(de::Unexpected::Str(s), &"never")),
            }
        }
    }

    impl<'de> Visitor<'de> for Never {
        type Value = Self;

        fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
            write!(formatter, "the string \"never\"")
        }

        fn visit_str<E>(self, s: &str) -> Result<Self::Value, E>
        where
            E: de::Error,
        {
            match s {
                "never" => Ok(Never),
                _ => Err(de::Error::invalid_value(de::Unexpected::Str(s), &self)),
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[derive(Serialize, Deserialize, PartialEq, Eq, Debug)]
        struct Thing {
            #[serde(with = "super")]
            value: Option<Duration>,
        }

        #[test]
        fn test() {
            let value = Some(Duration::from_millis(123));

            let s = toml::to_string(&Thing { value: None }).unwrap();
            assert_eq!(s, "value = \"never\"\n");
            assert_eq!(Thing { value: None }, toml::from_str(&s).unwrap());

            let s = toml::to_string(&Thing { value }).unwrap();
            assert_eq!(s, "value = 123\n");
            assert_eq!(Thing { value }, toml::from_str(&s).unwrap());

            assert!(toml::from_str::<Thing>(&"value = \"potato\"").is_err());
        }
    }
}

pub mod headers {
    use serde::{Deserialize, Deserializer, Serialize, Serializer, de, ser};
    use std::{fmt, time::Duration};

    use axum::http::header::{HeaderName, HeaderValue};

    #[derive(Default, Debug)]
    pub struct ExtraHeaders(pub Vec<(HeaderName, HeaderValue)>);

    impl Serialize for ExtraHeaders {
        fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
        where
            S: Serializer,
        {
            self.0
                .iter()
                .map(|(n, v)| {
                    Ok((
                        n.as_str().to_string(), /**/
                        v.to_str().map(toml::Value::from)?,
                    ))
                })
                .collect::<Result<toml::Table, axum::http::header::ToStrError>>()
                .map_err(ser::Error::custom)
                .and_then(|table| table.serialize(serializer))
        }
    }

    impl<'de> Deserialize<'de> for ExtraHeaders {
        fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
        where
            D: Deserializer<'de>,
        {
            return toml::Table::deserialize(deserializer)?
                .into_iter()
                .map(|(k, v)| {
                    Ok((
                        k.parse().map_err(de::Error::custom)?,
                        v.as_str()
                            .ok_or_else(|| {
                                de::Error::invalid_type(
                                    de::Unexpected::Other("not a string"),
                                    &"string",
                                )
                            })?
                            .parse()
                            .map_err(de::Error::custom)?,
                    ))
                })
                .collect::<Result<_, _>>()
                .map(ExtraHeaders);
        }
    }
}
