create table "clock"
    ( ts         integer primary key )
    strict;

insert into clock (ts) values (0);


create table "file"
    ( pk               integer primary key
    -- .tar.zstd bytes
    , data             blob
    -- uncompressed size
    , size             integer not null
    , etag             blob
    , check (size > 0)
    -- , check (data is null or volume is null)
    -- , check (not data is null and not volume is null)
    )
    strict;

create unique index "file/etag"
                 on "file" (etag)
              where etag is not null;


-- this is not unique on workshopid, multiple "versions" of an item can be
-- saved, when a new version is listed in the changelog
create table "workshop-item"
    ( pk                integer primary key
    , workshopid        text not null
    , title             text not null
    , author            text not null
    , version           integer not null
    , file              integer
                        references "file" (pk) )
    strict;

create unique index "workshop-item/workshopid-version"
                 on "workshop-item" (workshopid, version);


create table "content-package"
    ( pk                integer primary key
                        references "file" (pk)
    , name              text not null
    , version           text default '' )
    strict;


create table "build"
    ( pk                integer primary key
    , name              text not null
    , exit_code         integer
    , output            text not null default ''
    , fragment          blob not null default x'' )
    strict;


-- create table "build-result"
--     ( pk                integer primary key
--                         references "build" (pk)
--     , output            text
--     , fragment          blob )
--     strict;


/* No explicit primary key or unique constraint, so has an implicit rowid
 * primary key. Lax validation so allows duplicate items and sort numbers
 * but it's "best effort". */
create table "build-item"
    ( build             integer not null
                        references "build" (pk)
    , item              integer not null
                        references "workshop-item" (pk)
    , sort              integer not null )
    strict;

create index "build-item/build"
          on "build-item" (build);
create index "build-item/item"
          on "build-item" (item);


create table "publish"
    ( pk            integer primary key
    , public_url    text not null default ''
    , exit_code     integer
    , output        text not null default '' )
    strict;


create table "publish-item"
    ( publish       integer not null
                    references "publish"
    , build         integer not null
                    references "build" )
    strict;

create index "publish-item/publish"
          on "publish-item" (publish);

create index "publish-item/build-publish"
          on "publish-item" (build, publish);
