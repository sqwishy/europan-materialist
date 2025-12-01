use std::collections::{BTreeMap, btree_map};
use std::time::{Duration, Instant};

#[derive(Debug, PartialEq)]
pub struct OverLimit;

pub type Ticket = u16;

#[derive(Debug)]
pub struct Rated<T: Ord + Sized> {
    map: BTreeMap<T, Entry>,
    opt: Options,
    /* optimization; invariant is min of all entries' next_upkeep */
    next_retain: Option<Instant>,
}

impl<T: Ord + Sized> Rated<T> {
    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn options(&self) -> &Options {
        &self.opt
    }

    pub fn entry_sum(&mut self, key: &T) -> Option<Ticket> {
        let e = self.map.get_mut(key)?;
        e.upkeep(Instant::now(), &self.opt);
        e.sum()
    }

    // pub fn entry_len(&self) -> usize {
    //     self.opt.length
    // }

    // pub fn entry_size(&self) -> usize {
    //     core::mem::size_of::<Entry>().saturating_add(
    //         self.opt
    //             .length
    //             .saturating_mul(core::mem::size_of::<usize>()),
    //     )
    // }

    pub fn add(&mut self, key: T, value: Ticket) -> Result<(), OverLimit> {
        self.add_this_instant(key, value, Instant::now())
    }

    /* `now` must be at equal to or greater than the previous value of `now`.
     * if `now` ever decreases then this won't work good */
    pub fn add_this_instant(
        &mut self,
        key: T,
        value: Ticket,
        now: Instant,
    ) -> Result<(), OverLimit> {
        use btree_map::Entry::*;

        match self.next_retain {
            Some(moment) if moment <= now => self.map.retain(|_k, e| e.upkeep(now, &self.opt)),
            _ => (),
        };

        let entry = match self.map.entry(key) {
            Vacant(e) => e.insert(self.opt._new_entry(now)),
            Occupied(e) => e.into_mut(),
        };

        let r = entry.add(value, &self.opt);

        self.next_retain = self.map.values().map(|e| e.next_upkeep).min();

        r
    }
}

#[derive(Debug)]
struct Entry {
    earlier: Instant,
    /* the first element of `tickets` is for the bucket beginning at `earlier` */
    tickets: Vec<Ticket>,
    /* optimization; derived from earlier + Options.interval */
    next_upkeep: Instant,
}

impl Entry {
    fn add(&mut self, value: Ticket, o: &Options) -> Result<(), OverLimit> {
        match self.sum().and_then(|sum| sum.checked_add(value)) {
            Some(n) if n <= o.capacity => (),
            _ => return Err(OverLimit),
        }

        let Some(first) = self.tickets.get_mut(0) else {
            return Err(OverLimit);
        };

        *first = first.saturating_add(value);

        Ok(())
    }

    fn upkeep(&mut self, now: Instant, o: &Options) -> bool {
        if now < self.next_upkeep {
            return true;
        }

        let i = now
            .checked_duration_since(self.earlier)
            .and_then(|since| {
                (since.as_nanos())
                    .checked_div(o.interval.as_nanos())
                    .and_then(|n| usize::try_from(n).ok())
            })
            .unwrap_or(0usize);

        if i == 0 {
            return true;
        }

        if i < self.tickets.len() {
            self.tickets.rotate_right(i)
        }

        self.tickets.iter_mut().take(i).for_each(|v| *v = 0);

        if i < self.tickets.len() {
            self.earlier += (i as u32) * o.interval;
            self.next_upkeep = self.earlier + o.interval;
        } else {
            self.earlier = now;
            self.next_upkeep = self.earlier + o.interval;
        }

        return self.sum().unwrap_or(0) > 0;
    }

    fn sum(&self) -> Option<Ticket> {
        self.tickets
            .iter()
            .try_fold(0 as Ticket, |l, &r| l.checked_add(r))
    }
}

#[derive(Debug)]
pub struct Options {
    /// maximum ticket count for each entry
    pub capacity: Ticket,
    /// ticket amounts are not tracked at each moment, but
    /// instead histogram'd into buckets of `interval` size
    pub interval: Duration,
    // /// size of the entire sliding window thing, should be
    // /// a multiple of `interval` and at least `interval`
    // pub window: Duration,
    pub length: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            capacity: 1,
            interval: Duration::from_secs(1),
            length: 5,
        }
    }
}

impl Options {
    pub fn build<T: Ord + Sized>(self) -> Rated<T> {
        Rated {
            map: Default::default(),
            opt: self,
            next_retain: None,
        }
    }

    fn _new_entry(&self, earlier: Instant) -> Entry {
        Entry {
            earlier,
            next_upkeep: earlier + self.interval,
            tickets: vec![0; self.length],
        }
    }
}

#[cfg(test)]
const SEC: Duration = Duration::from_secs(1);

#[test]
fn test_options() {
    let r = Options {
        interval: Duration::from_secs(1),
        length: 3,
        capacity: 99,
        ..Options::default()
    }
    .build::<i8>();
    assert_eq!(r.opt.length, 3);

    let r = Options {
        interval: Duration::from_secs(33),
        length: 3,
        capacity: 99,
        ..Options::default()
    }
    .build::<i8>();
    assert_eq!(r.opt.length, 3);
}

#[test]
fn test_1() {
    let now = Instant::now();

    let mut r = Options {
        interval: Duration::from_secs(1),
        length: 3,
        capacity: 9,
        ..Options::default()
    }
    .build::<_>();
    assert_eq!(r.add_this_instant("", 9, now), Ok(()));
    assert_eq!(r.add_this_instant("", 1, now), Err(OverLimit));
    assert_eq!(r.add_this_instant("", 1, now + 1 * SEC), Err(OverLimit));
    assert_eq!(r.add_this_instant("", 1, now + 2 * SEC), Err(OverLimit));
    assert_eq!(r.add_this_instant("", 1, now + 3 * SEC), Ok(()));
}

#[test]
fn test_2() {
    let now = Instant::now();

    let mut r = Options {
        interval: Duration::from_secs(1),
        length: 3,
        capacity: 9,
        ..Options::default()
    }
    .build::<_>();
    assert_eq!(r.add_this_instant("", 3, now + 0 * SEC), Ok(()));
    assert_eq!(r.add_this_instant("", 6, now + 2 * SEC), Ok(()));
    assert_eq!(r.add_this_instant("", 1, now + 2 * SEC), Err(OverLimit));
    assert_eq!(r.add_this_instant("", 3, now + 3 * SEC), Ok(()));
    assert_eq!(r.add_this_instant("", 9, now + 9 * SEC), Ok(()));
    assert_eq!(r.add_this_instant("", 1, now + 11 * SEC), Err(OverLimit));
    assert_eq!(r.add_this_instant("", 9, now + 12 * SEC), Ok(()));
    assert_eq!(r.add_this_instant("", 10, now + 99 * SEC), Err(OverLimit));
}

#[test]
fn test_retain() {
    let now = Instant::now();

    let mut r = Options {
        interval: Duration::from_secs(1),
        length: 3,
        capacity: 9,
        ..Options::default()
    }
    .build::<_>();

    assert_eq!(r.add_this_instant("a", 1, now + 0 * SEC), Ok(()));
    assert_eq!(r.len(), 1);

    assert_eq!(r.add_this_instant("b", 1, now + 2 * SEC), Ok(()));
    assert_eq!(r.len(), 2);

    assert_eq!(r.add_this_instant("b", 1, now + 3 * SEC), Ok(()));
    assert_eq!(r.len(), 1);
}
