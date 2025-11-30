use rusqlite::{types::Value, Connection, ToSql};

use crate::butt;
use crate::no_args;

pub type Pragmas = std::collections::HashMap<String, PragmaValue>;

#[derive(Debug)]
pub struct PragmaValue(Value);

impl<'de> serde::Deserialize<'de> for PragmaValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let v = match toml::Value::deserialize(deserializer)? {
            toml::Value::Integer(v) => Value::Integer(v),
            toml::Value::Float(v) => Value::Real(v),
            toml::Value::Boolean(false) => Value::Integer(0),
            toml::Value::Boolean(true) => Value::Integer(1),
            toml::Value::String(v) => Value::Text(v),
            toml::Value::Table(_) | toml::Value::Array(_) | toml::Value::Datetime(_) => {
                use serde::de::Error;
                return Err(D::Error::custom(
                    "no appropriate sql value for toml tables, arrays, or datetime",
                ));
            }
        };
        Ok(PragmaValue(v))
    }
}

impl serde::Serialize for PragmaValue {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        match &self.0 {
            Value::Integer(v) => toml::Value::Integer(*v),
            Value::Real(v) => toml::Value::Float(*v),
            Value::Text(v) => toml::Value::String(v.clone()),
            /* there are appropriate serializer methods for these that might _work_ but
             * may not do the _correct_ thing in that deserializing the value back would
             * give us a different value ... since this is not a real use case, better
             * fail than possibly do the wrong thing */
            Value::Null | Value::Blob(_) => {
                use serde::ser::Error;
                Err(S::Error::custom("cannot serialize sql Null or Blob :("))?
            }
        }
        .serialize(serializer)
    }
}

pub fn apply(conn: &Connection, pragmas: &Pragmas) {
    for (k, v) in pragmas.iter() {
        let v = &v.0;
        let was = conn.pragma_query_value(None, k, |row| row.get::<_, Value>(0));
        let ret =
            conn.pragma_update_and_check(None, k, &v as &dyn ToSql, |row| row.get::<_, Value>(0));
        let now = conn.pragma_query_value(None, k, |row| row.get::<_, Value>(0));
        butt!("pragma update"; "pragma" => k, "was" => crate::debug(&was), "update" => crate::debug(&v), "returned" => crate::debug(&ret), "now" => crate::debug(&now));
    }
}
