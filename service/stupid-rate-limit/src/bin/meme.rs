use std::time::{Duration, Instant};

use stupid_rate_limit::Options;

fn main() {
    let mut now = Instant::now();

    let mut r = Options {
        interval: Duration::from_secs(60),
        length: 120,
        /* adding 1/sec for 120 * 60 = 7200 secs */
        capacity: 7200,
        ..Options::default()
    }
    .build::<u16>();

    for n in 0..1000 {
        for i in 0..100 {
            r.add_this_instant(n + i, 1, now).unwrap();
        }

        now = now + Duration::from_secs(1);
    }
}
