create table "steam_workshop_item"
    ( pk                integer not null primary key
    , publishedfileid   text    not null
    , consumer_app_id   integer not null
    -- normally joinable on steam_player.steamid
    , creator           text    not null
    , title             text    not null
    , preview_url       text
    , time_created      integer not null
    , time_updated      integer not null);

create unique index "steam_workshop_item/publishedfileid"
                 on "steam_workshop_item" (publishedfileid);


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


create table "scheduled"
    (
    );

create table "steamcmd_download"
    ( pk                integer not null primary key
    , session           integer not null
    , state             integer not null
    );

-- create table "steamcmd"
--     ( pk                integer not null primary key
--     , output            text
--     , exitcode          integer
--     );
