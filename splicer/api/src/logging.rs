//! I managed to litter logging code with woofers::debug just so that I could get strings to format
//! properly and all along I probably should have used a trait and implemented it for the types I
//! wanted to support below .... yikes
use std::borrow::Borrow;
use std::borrow::Cow;
use std::fmt;
use std::io::{self, Write};
use std::marker::PhantomData;
use std::os::unix::io::{AsRawFd, RawFd};

use libc::{LOG_DEBUG, LOG_ERR, LOG_INFO, LOG_WARNING};
use libsystemd_sys::const_iovec;
use libsystemd_sys::daemon::sd_is_socket;
use libsystemd_sys::journal::sd_journal_sendv;

use jiff::{Unit, Zoned};

use crate::ansi;

#[allow(dead_code)]
#[derive(Debug, Clone, Copy)]
pub enum Level {
    Butt,
    Info,
    Warn,
    Crit,
}

impl Level {
    fn as_str(&self) -> &'static str {
        match self {
            Level::Butt => "butt",
            Level::Info => "info",
            Level::Warn => "warn",
            Level::Crit => "crit",
        }
    }

    fn color(&self) -> ansi::Ansi {
        match self {
            Level::Butt => ansi::blue,
            Level::Info => ansi::green,
            Level::Warn => ansi::yellow,
            Level::Crit => ansi::red,
        }
    }

    /// The priority value is one of LOG_EMERG, LOG_ALERT, LOG_CRIT, LOG_ERR, LOG_WARNING,
    /// LOG_NOTICE, LOG_INFO, LOG_DEBUG, as defined in syslog.h
    fn as_priority(&self) -> libc::c_int {
        match self {
            Level::Butt => LOG_DEBUG,
            Level::Info => LOG_INFO,
            Level::Warn => LOG_WARNING,
            Level::Crit => LOG_ERR,
        }
    }
}

impl fmt::Display for Level {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.as_str().fmt(f)
    }
}

#[derive(Debug, Clone)]
pub struct FileLineNo(pub &'static str, pub u32);

impl FileLineNo {
    fn file(&self) -> &'static str {
        self.0
    }
    fn line(&self) -> u32 {
        self.1
    }
}

impl fmt::Display for FileLineNo {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.0, self.1)
    }
}

pub struct Event<'a> {
    pub lvl: Level,
    pub wher: FileLineNo,
    pub when: Zoned,
    pub what: &'a str,
    pub kv: &'a [KeyValue<'a>],
}

pub struct KeyValue<'a> {
    pub k: &'static str,
    pub v: &'a dyn fmt::Display,
}

fn fmt_kv_slice<C, D>(kvs: &[KeyValue], color: C) -> String
where
    C: Fn(&'static str) -> D,
    D: fmt::Display,
{
    use std::fmt::Write;
    let mut s = String::new();
    for KeyValue { k, v } in kvs.iter() {
        let _ = write!(s, "\n\t» {} {}", color(k), v);
    }
    s
}

pub trait Logger {
    fn write_event(&mut self, e: &Event) -> io::Result<()>;

    fn log<'a>(
        &mut self,
        lvl: Level,
        what: &str,
        wher: FileLineNo,
        kv: &[KeyValue<'a>],
    ) {
        let ev = Event { lvl, when: Zoned::now(), wher, what, kv };
        let _ = self.write_event(&ev);
    }
}

pub struct WriteLogger<W: Write> {
    w: W,
    use_colors: bool,
}

impl<W: Write> Logger for WriteLogger<W> {
    fn write_event(&mut self, e: &Event) -> io::Result<()> {
        let when = e.when.strftime("%_d %b %Y %T%.3f");

        if self.use_colors {
            writeln!(
                self.w,
                "{} {} {}  {}{}",
                ansi::dark_grey.fg(when),
                e.lvl.color().fg(&e.lvl),
                ansi::italic.fg(&e.wher),
                e.what,
                fmt_kv_slice(e.kv, |s| ansi::magenta.fg(s)),
            )
        } else {
            writeln!(
                self.w,
                "{} {} {}\t{}{}",
                when,
                e.lvl,
                e.wher,
                e.what,
                fmt_kv_slice(e.kv, std::convert::identity),
            )
        }?;

        /* do I need to call this??? */
        self.w.flush()
    }
}

pub struct JournalLogger {
    prefix: Cow<'static, str>,
}

impl Logger for JournalLogger {
    fn write_event(&mut self, e: &Event) -> io::Result<()> {
        /* TODO use bytes instead of strings? -- i think most of this is not quite utf-8 */
        let msg = {
            let kv = fmt_kv_slice(e.kv, std::convert::identity);
            format!("{}{}", e.what, kv)
        };
        let mut entries = vec![
            format!("MESSAGE={}", msg),
            format!("PRIORITY={}", e.lvl.as_priority()),
            format!("CODE_FILE={}", e.wher.file()),
            format!("CODE_LINE={}", e.wher.line()),
        ];
        for KeyValue { k, v } in e.kv.iter() {
            let k = clean_journal_key(k);
            debug_assert_ne!(k, "");
            if !k.is_empty() {
                entries.push(format!("{}_{}={}", self.prefix, k, v))
            }
        }

        let iovecs = entries
            .iter()
            .map(|s| unsafe { const_iovec::from_str(s) })
            .collect::<Vec<_>>();
        match unsafe { sd_journal_sendv(iovecs.as_ptr(), iovecs.len() as libc::c_int) }
        {
            ret if ret < 0 => Err(io::Error::from_raw_os_error(ret)),
            _ => Ok(()),
        }
    }
}

/// The variable name must be in uppercase and consist only of characters,
/// numbers and underscores, and may not begin with an underscore.
///
/// This can return an empty string.
fn clean_journal_key(dirty: &'_ str) -> Cow<'_, str> {
    /* skip over any byte that would map to _ */
    let safe_start = dirty.trim_start_matches(|c: char| -> bool {
        !matches!(c, 'a'..='z' | 'A'..='Z' | '0'..='9')
    });

    let mut key = Cow::from(safe_start.as_bytes());
    for i in 0..key.len() {
        match key[i] {
            b'A'..=b'Z' | b'0'..=b'9' => (),
            b'a'..=b'z' => key.to_mut()[i] = key[i].to_ascii_uppercase(),
            _ => key.to_mut()[i] = b'_',
        }
    }

    match key {
        Cow::Owned(o) => String::from_utf8(o)
            .expect("sanitized key should be valid utf-8?")
            .into(),
        Cow::Borrowed(_) => Cow::Borrowed(safe_start),
    }
}

pub fn new_logger<W>(w: W) -> Box<dyn Logger>
where
    W: Write + AsRawFd + 'static,
{
    if fd_is_journald(w.as_raw_fd()) {
        let prefix = "LOG".into(); /* TODO figure out a better way to get context. */
        Box::new(JournalLogger { prefix })
    } else {
        let use_colors = isatty(&w);
        Box::new(WriteLogger { w, use_colors })
    }
}

#[cfg(target_family = "unix")]
pub fn isatty<T: AsRawFd>(t: &T) -> bool {
    1 == unsafe { libc::isatty(t.as_raw_fd()) }
}

/// this ignores errors
fn fd_is_journald(fd: RawFd) -> bool {
    1 == unsafe {
        sd_is_socket(
            fd,
            libc::AF_UNIX,
            0, /* match all type (dgram, stream) */
            0, /* match not listening */
        )
    }
}

/// todo make this useful
pub fn time<D: fmt::Debug>(d: D) -> impl fmt::Display {
    dbg(d)
}

pub fn dbg<D: fmt::Debug>(d: D) -> impl fmt::Display {
    DebugAsDisplay(d)
}

struct DebugAsDisplay<T>(T);

impl<D: fmt::Debug> fmt::Display for DebugAsDisplay<D> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}", self.0)
    }
}

pub fn result<R, T, E>(r: R) -> impl fmt::Display
where
    R: Borrow<Result<T, E>>,
    T: fmt::Display,
    E: fmt::Display,
{
    ResultDisplay(r, Default::default())
}

struct ResultDisplay<R, T, E>(R, PhantomData<(T, E)>);

impl<R, T, E> fmt::Display for ResultDisplay<R, T, E>
where
    R: Borrow<Result<T, E>>,
    T: fmt::Display,
    E: fmt::Display,
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.0.borrow() {
            Ok(ok) => write!(f, "Ok#{}", ok),
            Err(err) => write!(f, "Err#{}", err),
        }
    }
}

/// uses anyhow's error formatting to display error causes
///
/// this might create a backtrace if the given thing doesn't have one, which is
/// a bit clumsy since we will appeaer in the trace, but we never show/care about
/// backtraces so whatever
pub fn err<E>(e: E) -> impl fmt::Display
where
    anyhow::Error: From<E>,
{
    // alt(anyhow::Error::from(e))
    dbg(anyhow::Error::from(e))
}

pub fn alt<D: fmt::Display>(d: D) -> impl fmt::Display {
    AltDisplay(d)
}

struct AltDisplay<D>(D);

impl<D: fmt::Display> fmt::Display for AltDisplay<D> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:#}", self.0)
    }
}

/// displays Option<impl fmt::Display> without Some
pub fn opt<D: fmt::Display>(d: Option<D>) -> impl fmt::Display {
    OptDisplay(d)
}

struct OptDisplay<D>(Option<D>);

impl<'a, D: fmt::Display> fmt::Display for OptDisplay<D> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.0 {
            Some(d) => write!(f, "{}", d),
            None => write!(f, "None"),
        }
    }
}

thread_local! {
    pub static LOGGER: std::cell::RefCell<Box<dyn Logger>> = new_logger(io::stderr()).into();
}

#[macro_export]
macro_rules! _log {
    ($lvl:ident $msg:literal $(; $($kv:tt)*)?) => {
        $crate::logging::LOGGER.with(|l| {
            l.borrow_mut().log($crate::logging::Level::$lvl, $msg, $crate::logging::FileLineNo(file!(), line!()), $crate::_kv![$($($kv)*)*])
        })
    };
    ($lvl:ident $fmt:expr, $($arg:tt)* $(; $($kv:tt)*)?) => {
        $crate::logging::LOGGER.with(|l| {
            l.borrow_mut().log($crate::logging::Level::$lvl, &format!($fmt, $($arg)*), $crate::logging::FileLineNo(file!(), line!()), Default::default())
        })
    };
}

#[macro_export]
macro_rules! _kv {
    ($($k:expr => $v:expr),*) => {
        &[$($crate::logging::KeyValue { k: $k, v: &$v }),*]
    }
}

#[macro_export]
macro_rules! butt {
    ($($arg:tt)*) => { $crate::_log!(Butt $($arg)*) }
}

#[macro_export]
macro_rules! info {
    ($($arg:tt)*) => { $crate::_log!(Info $($arg)*) }
}

#[macro_export]
macro_rules! warn {
    ($($arg:tt)*) => { $crate::_log!(Warn $($arg)*) }
}

#[macro_export]
macro_rules! crit {
    ($($arg:tt)*) => { $crate::_log!(Crit $($arg)*) }
}

#[cfg(test)]
mod tests {
    /// running this test under systemd-run log a bit differently
    /// since new_logger will use JournalLogger
    #[test]
    fn test() {
        butt!("wow: {}", 420);
        info!("hello world");
        warn!("yikes");
        crit!("oof"; "happy" => super::dbg(&vec!["ducks"]), "owo" => 69);
    }
}
