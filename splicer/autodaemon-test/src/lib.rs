#![allow(unused)]

use std::sync::Arc;
use std::sync::Mutex;

use async_channel::{Receiver, Sender, bounded as bounded_channel};

use autodaemon::autodaemon;

#[derive(Debug)]
pub struct Daemon {
    i: u64,
    r: Receiver<Query>,
}

#[autodaemon(dispatch)]
impl Daemon {
    fn inc(&mut self, inc: u64) -> u64 {
        self.i = self.i.saturating_add(inc);
        self.i
    }

    pub fn read(&self) -> u64 {
        self.i
    }

    // async fn aread(&self) -> u64 {
    //     self.i
    // }

    fn swap(&mut self, cell: Arc<Mutex<u64>>) -> u64 {
        todo!();
    }
}

impl Daemon {
    async fn tick(&mut self) -> bool {
        let q = self.r.recv().await.unwrap();
        self.dispatch(q)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_works() {
        let (s, r) = bounded_channel(1);
        let mut d = Daemon { i: 0, r };
        let c = Client { s };

        smol::block_on(async {
            assert_eq!(d.i, 0);
            let r = c.inc(3).await.expect("sender connected");
            assert!(d.tick().await);
            assert_eq!(d.i, 3);
            assert_eq!(r.recv().await.expect("receiver connected"), 3);
        })
    }

    #[test]
    fn drop_early() {
        let (s, r) = bounded_channel(1);
        let mut d = Daemon { i: 0, r };
        let c = Client { s };

        smol::block_on(async {
            let r = c.inc(3).await.expect("sender connected");
            drop(r);
            assert_eq!(d.tick().await, false);
            assert_eq!(d.i, 0);
        })
    }
}
