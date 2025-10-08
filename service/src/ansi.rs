#![allow(unused)]
#![allow(non_upper_case_globals)]

use std::fmt::{self, Display};

/// 256-color mode https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit
pub struct Ansi(&'static str);

impl Ansi {
    pub fn fg<D: Display>(&self, inner: D) -> Fg<D> {
        let fg = self.0;
        Fg { fg, inner }
    }
}

pub struct Fg<D: Display> {
    fg: &'static str,
    inner: D,
}

impl<D: Display> Display for Fg<D> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let Fg { fg, inner } = self;
        write!(f, "{}{}{}", fg, inner, reset)
    }
}

pub const reset: &str = "\x1b[0m";
// ... f"\x1b[38;5;{n}m"
pub const black: Ansi = Ansi("\x1b[38;5;0m");
pub const dark_red: Ansi = Ansi("\x1b[38;5;1m");
pub const dark_green: Ansi = Ansi("\x1b[38;5;2m");
pub const dark_yellow: Ansi = Ansi("\x1b[38;5;3m");
pub const dark_blue: Ansi = Ansi("\x1b[38;5;4m");
pub const dark_magenta: Ansi = Ansi("\x1b[38;5;5m");
pub const dark_teal: Ansi = Ansi("\x1b[38;5;6m");
pub const grey: Ansi = Ansi("\x1b[38;5;7m");
pub const dark_grey: Ansi = Ansi("\x1b[38;5;8m");
pub const red: Ansi = Ansi("\x1b[38;5;9m");
pub const green: Ansi = Ansi("\x1b[38;5;10m");
pub const yellow: Ansi = Ansi("\x1b[38;5;11m");
pub const blue: Ansi = Ansi("\x1b[38;5;12m");
pub const magenta: Ansi = Ansi("\x1b[38;5;13m");
pub const teal: Ansi = Ansi("\x1b[38;5;14m");
pub const white: Ansi = Ansi("\x1b[38;5;15m");
