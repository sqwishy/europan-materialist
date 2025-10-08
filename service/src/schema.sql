create table "steam_workshop_item"
    ( pk                integer not null primary key
    , publishedfileid   text    not null
    , consumer_app_id   integer not null
    -- normally joinable on steam_player.steamid
    , creator           text    not null
    , title             text    not null
    , preview_url       text
    , time_created      integer not null
    , time_updated      integer not null );

create unique index "steam_workshop_item/publishedfileid"
                 on "steam_workshop_item" (publishedfileid);


create table "steam_workshop_collection_entry"
    ( pk                integer not null primary key
    , publishedfileid   text    not null
    , sortorder         integer not null );


create table "steam_player"
    ( pk                integer not null primary key
    , steamid           text    not null
    , personaname       text    not null
    , profileurl        text    not null
    , avatar            text
    , avatarmedium      text
    , avatarmediumfull  text );

create unique index "steam_player/steamid"
                 on "steam_player" (steamid);

-- - There can be multiple downloads for a workshop item,
--   of differing versions.
--
-- - The steamapi workshop item details are recorded with
--   the downloaded item files?

-- zstd encrypted workshop item files
create table "download"
    ( pk                integer not null primary key
    , timestamp         integer not null
    , data              blob    not null
    , size              integer not null
    );

-- -- scheduled stuff always has a session cookie that can be
-- -- used to verify if it's stale
-- create table "schedule"
--     ( pk                integer not null primary key
--     , session           integer not null
--     );

-- create table "steamcmd_download"
--     ( pk                integer not null primary key
--     , session           integer not null
--     , state             integer not null
--     );

-- create table "steamcmd"
--     ( pk                integer not null primary key
--     , output            text
--     , exitcode          integer
--     );

create table "steamapi_workshop_collection_poll"
    ( pk                integer not null primary key
    , ts                integer not null
    , publishedfileid   text    not null
    , response          integer references "steamapi_workshop_collection" (pk)
    )

create unique index "steam_workshop_collection_poll/publishedfileid-ts"
                 on "steam_workshop_collection_poll" (publishedfileid, ts);

create table "steamapi_workshop_item"
    ( pk                integer not null primary key
    , ts                integer not null
    , publishedfileid   text    not null
    )
create unique index "steam_workshop_item/publishedfileid-ts"
                 on "steam_workshop_item" (publishedfileid, ts);
