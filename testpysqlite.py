from pysqlite2 import dbapi2 as sqlite

con = sqlite.connect(":memory:")
con.row_factory = sqlite.Row

create_tables = """
CREATE TABLE artists (
  id             INTEGER CONSTRAINT pk_artist_id PRIMARY KEY AUTOINCREMENT,
  name           TEXT UNIQUE
);

CREATE TABLE albums (
  id             INTEGER CONSTRAINT pk_album_id PRIMARY KEY AUTOINCREMENT,
  artist_id      INTEGER CONSTRAINT fk_albums_artist_id REFERENCES artists(id),
  name           TEXT,
  UNIQUE (artist_id, name)
);

CREATE TABLE genres (
  id             INTEGER CONSTRAINT pk_genre_id PRIMARY KEY AUTOINCREMENT,
  name           TEXT UNIQUE
);

CREATE TABLE covers (
  id             INTEGER CONSTRAINT pk_cover_id PRIMARY KEY,
  image          BLOB UNIQUE
);

CREATE TABLE tags (
  id             INTEGER CONSTRAINT pk_tag_id PRIMARY KEY AUTOINCREMENT,
  name           TEXT UNIQUE
);

CREATE TABLE songtags (
  song_id        INTEGER CONSTRAINT fk_song_id REFERENCES songs(id),
  tag_id         INTEGER CONSTRAINT fk_tag_id  REFERENCES tags(id)
);

CREATE TABLE playstats (
  song_id        INTEGER CONSTRAINT fk_song_id REFERENCES songs(id),
  playtime       TIMESTAMP
);

CREATE TABLE songs (
  id                    TEXT CONSTRAINT pk_song_id PRIMARY KEY,
  url                   TEXT,
  type                  TEXT,
  title                 TEXT,
  album_id              INTEGER CONSTRAINT fk_song_album_id  REFERENCES albums(id),
  artist_id             INTEGER CONSTRAINT fk_song_artist_id REFERENCES artists(id),
  genre_id              INTEGER CONSTRAINT fk_song_genre_id  REFERENCES genres(id),
  cover_id              INTEGER CONSTRAINT fk_song_cover_id  REFERENCES covers(id),
  year                  INTEGER,
  comment               TEXT,
  lyrics                TEXT,
  length                INTEGER,
  tracknumber           INTEGER,
  trackcount            INTEGER,
  disknumber            INTEGER,
  diskcount             INTEGER,
  bitrate               INTEGER,
  vbr                   BOOT,
  samplerate            INTEGER,
  replaygain_track_gain FLOAT,
  replaygain_track_peak FLOAT,
  replaygain_album_gain FLOAT,
  replaygain_album_peak FLOAT,
  size                  INTEGER,
  collection            BOOL,
  date_added            TIMESTAMP,
  date_changed          TIMESTAMP,
  date_lastplayed       TIMESTAMP,
  playcount             INTEGER,
  rating                FLOAT
);

CREATE INDEX album_id ON albums(name);
CREATE INDEX artist_id ON artists(name);
CREATE INDEX genre_id ON genres(name);
CREATE INDEX tag_id ON tags(name);

CREATE INDEX album_id_song ON songs(album_id);
CREATE INDEX artist_id_song ON songs(artist_id);
CREATE INDEX genre_id_song ON songs(genre_id);
CREATE INDEX year_song ON songs(year);
CREATE INDEX collection_song ON songs(collection);
"""

con.executescript(create_tables)

class song:
    def __init__(self, title, album, artist, genre):
        self.title = title
        self.album = album
        self.artist = artist
        self.genre = genre

cur = con.cursor()

br = song("Bohemian Rapsody", "Greatest Hits", "Queen", "Rock")
wywh = song("Wish You Were Here", "Wish You Were Here", "Pink Floyd", "PsychedelicPsychedelic  Rock")

def insertsong(song):
    cur.execute("SELECT * FROM artists WHERE name=?", (song.artist,))
    r = cur.fetchone()
    if r is None:
        con.execute("INSERT INTO artists (name) VALUES (?)", (song.artist,))
        cur.execute("SELECT * FROM artists WHERE name=?", (song.artist,))
        r = cur.fetchone()
    song.artist_id = r["id"]

    cur.execute("SELECT * FROM albums WHERE name=? AND artist_id=?", (song.album, song.artist_id))
    r = cur.fetchone()
    if r is None:
        con.execute("INSERT INTO albums (name, artist_id) VALUES (?, ?)", (song.album, song.artist_id))
        cur.execute("SELECT * FROM albums WHERE name=? AND artist_id=?", (song.album, song.artist_id))
        r = cur.fetchone()
    song.album_id = r["id"]

    cur.execute("SELECT * FROM genres WHERE name=?", (song.genre,))
    r = cur.fetchone()
    if r is None:
        con.execute("INSERT INTO genres (name) VALUES (?)", (song.genre,))
        cur.execute("SELECT * FROM genres WHERE name=?", (song.genre,))
        r = cur.fetchone()
    song.genre_id = r["id"]

    cur.execute("""INSERT INTO songs (title, artist_id, album_id, genre_id) 
                 VALUES (?, ?, ?, ?)""", (song.title, song.artist_id, song.album_id, song.genre_id))

insertsong(br)
insertsong(wywh)
con.commit()

r = con.execute("""SELECT songs.title, artists.name AS artist, albums.name AS album
                   FROM songs
                   JOIN artists ON (songs.artist_id = artists.id)
                   JOIN albums ON (songs.album_id = albums.id)
                   """)
for c in r.fetchall():
    print c["title"], "-", c["artist"], "-", c["album"]


#INSERT into songs (id, url, name, artist_id, album_id, genre_id) values 
#    ("a", "file://123", 

