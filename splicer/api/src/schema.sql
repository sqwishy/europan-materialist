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


create table "mod-list"
    ( pk                integer primary key )
    strict;


/* Has an implicit rowid primary key.
 * Lax validation, allows duplicate items and sort numbers ...
 * ... it's "best effort". */
create table "mod-list-item"
    ( list              integer not null
                        references "mod-list" (pk)
    , item              integer not null
                        references "workshop-item" (pk)
    , sort              integer not null )
    strict;

create index "mod-list-item/list"
          on "mod-list-item" (list);

create index "mod-list-item/item"
          on "mod-list-item" (item);


create table "build"
    ( pk                integer primary key
                        references "mod-list" (pk)
    , exit_code         integer
    , output            text not null default ''
    , fragment          blob not null default x'' )
    strict;


create table "publish"
    ( pk            integer primary key
    , exit_code     integer
    , output        text not null default '' )
    strict;


create table "publish-item"
    ( publish       integer not null
                    references "publish"
    , build         integer not null
                    references "build"
    , url           text not null default '' )
    strict;

create index "publish-item/publish"
          on "publish-item" (publish DESC);

create index "publish-item/build-publish"
          on "publish-item" (build, publish DESC);
