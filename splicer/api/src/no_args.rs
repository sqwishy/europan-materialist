use std::io;
use std::path::{Path, PathBuf};

use anyhow::{format_err, Context};

use crate::ansi;

pub struct NoArgs {
    path: PathBuf,
}

impl NoArgs {
    pub fn path(&self) -> &Path {
        self.path.as_ref()
    }

    /// can resolve an absolute path to the file, may be useful in case the process changes
    /// directories and wants a more stable reference or something
    pub fn canonicalize(&mut self) -> io::Result<()> {
        self.path = self.path.canonicalize()?;
        Ok(())
    }

    pub fn read_and_parse(&self) -> anyhow::Result<api_config::Config> {
        let s = std::fs::read_to_string(&self.path).with_context(|| {
            format_err!("read configuration at {}", self.path.display())
        })?;
        Self::parse(&s).with_context(|| {
            format_err!("parse configuration at {}", self.path.display())
        })
    }

    pub fn parse(s: &str) -> api_config::Result<api_config::Config> {
        api_config::decode(s)
    }
}

pub fn from_argv<P: AsRef<Path>>(name: &str, default: P) -> NoArgs {
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
                "--check" => {
                    only_show = true;
                }
                "--show-default" => {
                    println!("{}", api_config::DEFAULT_CONFIG);
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

    let no = NoArgs { path };

    if only_show {
        match no.read_and_parse() {
            Ok(t) => {
                eprintln!("{} » {}", no.path.display(), ansi::blue.fg("success"));
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
            "{} » {} [--show-default] (or) [--check] [{}]\n\
            \t--check       \tread the config & exit.\n\
            \t              \tfails when config can't be read or is busted\n\
            \t--show-default\tshow default config file to stdout and exit",
            ansi::blue.fg("usage"),
            exe,
            path
        )
    }
}
