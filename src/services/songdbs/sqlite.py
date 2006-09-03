
# -*- coding: ISO-8859-1 -*-

# Copyright (C) 2002, 2003, 2004, 2006 J�rg Lehmann <joerg@luga.de>
#
# This file is part of PyTone (http://www.luga.de/pytone/)
#
# PyTone is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2
# as published by the Free Software Foundation.
#
# PyTone is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PyTone; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import os
import errno
import sys
import time

from pysqlite2 import dbapi2 as sqlite

import events, hub, requests
import errors
import log
import metadata
import dbitem
import item
import service


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

CREATE TABLE tags (
  id             INTEGER CONSTRAINT pk_tag_id PRIMARY KEY AUTOINCREMENT,
  name           TEXT UNIQUE
);

CREATE TABLE taggings (
  song_id        INTEGER CONSTRAINT fk_song_id REFERENCES songs(id),
  tag_id         INTEGER CONSTRAINT fk_tag_id  REFERENCES tags(id)
);

CREATE TABLE playstats (
  song_id        INTEGER CONSTRAINT fk_song_id REFERENCES songs(id),
  playtime       TIMESTAMP
);

CREATE TABLE songs (
  id                    INTEGER CONSTRAINT pk_song_id PRIMARY KEY AUTOINCREMENT,
  url                   TEXT UNIQUE,
  type                  TEXT,
  title                 TEXT,
  album_id              INTEGER CONSTRAINT fk_song_album_id  REFERENCES albums(id),
  artist_id             INTEGER CONSTRAINT fk_song_artist_id REFERENCES artists(id),
  album_artist_id       INTEGER CONSTRAINT fk_song_artist_id REFERENCES artists(id),
  year                  INTEGER,
  comment               TEXT,
  lyrics                TEXT,
  length                INTEGER,
  tracknumber           INTEGER,
  trackcount            INTEGER,
  disknumber            INTEGER,
  diskcount             INTEGER,
  compilation           BOOL,
  bitrate               INTEGER,
  is_vbr                BOOT,
  samplerate            INTEGER,
  replaygain_track_gain FLOAT,
  replaygain_track_peak FLOAT,
  replaygain_album_gain FLOAT,
  replaygain_album_peak FLOAT,
  size                  INTEGER,
  date_added            TIMESTAMP,
  date_updated          TIMESTAMP,
  date_lastplayed       TIMESTAMP,
  playcount             INTEGER,
  rating                FLOAT
);

CREATE INDEX album_id ON albums(name);
CREATE INDEX artist_id ON artists(name);
CREATE INDEX tag_id ON tags(name);

CREATE INDEX url_song ON songs(url);
CREATE INDEX album_id_song ON songs(album_id);
CREATE INDEX artist_id_song ON songs(artist_id);
CREATE INDEX year_song ON songs(year);
CREATE INDEX compilation_song ON songs(compilation);

CREATE INDEX taggings_song_id ON taggings(song_id);
CREATE INDEX taggings_tag_id ON taggings(tag_id);
"""

songcolumns = ["url", "type", "title", "album_id",
               "artist_id", "album_artist_id", "year", "comment", "lyrics",
               "length", "tracknumber", "trackcount", "disknumber", "diskcount",
               "compilation", "bitrate", "is_vbr", "samplerate", 
               "replaygain_track_gain", "replaygain_track_peak",
               "replaygain_album_gain", "replaygain_album_peak", 
               "size", "compilation", "date_added", "date_updated", "date_lastplayed", 
               "playcount", "rating"]


#
# statistical information about songdb
#

class songdbstats:
    def __init__(self, id, type, basedir, location, dbfile, cachesize,
                 numberofsongs, numberofalbums, numberofartists, numberoftags):
        self.id = id
        self.type = type
        self.basedir = basedir
        self.location = location
        self.dbfile = dbfile
        self.cachesize = cachesize
        self.numberofsongs = numberofsongs
        self.numberofalbums = numberofalbums
        self.numberofartists = numberofartists
        self.numberoftags = numberoftags

#
# songdb class
#

class songdb(service.service):

    currentdbversion = 1

    def __init__(self, id, config, songdbhub):
        service.service.__init__(self, "%r songdb" % id, hub=songdbhub)
        self.id = id
        self.basedir = config.musicbasedir
        self.dbfile = config.dbfile
        self.cachesize = config.cachesize
        self.playingstatslength = config.playingstatslength

        if not os.path.isdir(self.basedir):
            raise errors.configurationerror("musicbasedir '%r' of database %r is not a directory." % 
					    (self.basedir, self.id))

        if not os.access(self.basedir, os.X_OK | os.R_OK):
            raise errors.configurationerror("you are not allowed to access and read config.general.musicbasedir.")

        # currently active cursor - initially, none
        self.cur = None

        # we need to be informed about database changes
        self.channel.subscribe(events.addsong, self.addsong)
        self.channel.subscribe(events.updatesong, self.updatesong)
        self.channel.subscribe(events.delsong, self.delsong)

        self.channel.subscribe(events.updateplaylist, self.updateplaylist)
        self.channel.subscribe(events.delplaylist, self.delplaylist)

        self.channel.subscribe(events.registerplaylists, self.registerplaylists)

        self.channel.subscribe(events.clearstats, self.clearstats)

        # we are a database service provider...
        self.channel.supply(requests.getdatabasestats, self.getdatabasestats)
        self.channel.supply(requests.getsong, self.getsong)
        self.channel.supply(requests.getartists, self.getartists)
        self.channel.supply(requests.getalbums, self.getalbums)
        self.channel.supply(requests.getalbum, self.getalbum)
        self.channel.supply(requests.getartist, self.getartist)
        self.channel.supply(requests.getsongs, self.getsongs)
        self.channel.supply(requests.getnumberofsongs, self.getnumberofsongs)
        self.channel.supply(requests.getnumberofalbums, self.getnumberofalbums)
        self.channel.supply(requests.getnumberofartists, self.getnumberofartists)
        self.channel.supply(requests.getnumberoftags, self.getnumberoftags)
        self.channel.supply(requests.getnumberofratings, self.getnumberofratings)
        self.channel.supply(requests.gettags, self.gettags)
        self.channel.supply(requests.getratings, self.getratings)
        self.channel.supply(requests.getlastplayedsongs, self.getlastplayedsongs)
        self.channel.supply(requests.gettopplayedsongs, self.gettopplayedsongs)
        self.channel.supply(requests.getlastaddedsongs, self.getlastaddedsongs)
        self.channel.supply(requests.getplaylist, self.getplaylist)
        self.channel.supply(requests.getplaylists, self.getplaylists)
        self.channel.supply(requests.getsongsinplaylist, self.getsongsinplaylist)
        self.channel.supply(requests.getsongsinplaylists, self.getsongsinplaylists)

        self.autoregisterer = songautoregisterer(self.basedir, self.id, self.isbusy,
                                                 config.tracknrandtitlere,
                                                 config.tags_capitalize, config.tags_stripleadingarticle, 
						 config.tags_removeaccents)
        self.autoregisterer.start()

    def run(self):
        # self.con = sqlite.connect(":memory:")
	log.debug("dbfile: '%s'" % self.dbfile)
	self.con = sqlite.connect(self.dbfile)
        self.con.row_factory = sqlite.Row

	dbversion = self.con.execute("PRAGMA user_version").fetchone()[0]
	log.debug("Found on-disk db version: %d" % dbversion)
	if dbversion == 0:
	    # fresh database
	    self._txn_begin()
	    self.con.executescript(create_tables)
	    self._txn_commit()
	    self.con.execute("PRAGMA user_version=%d" % self.currentdbversion)
        service.service.run(self)
        self.close()

    def close(self):
        self.con.close()

    # transaction machinery

    def _txn_begin(self):
        if self.cur:
            raise RuntimeError("more than one transaction in parallel is not supported")
        # self.con.execute("BEGIN TRANSACTION")
        self.cur = self.con.cursor()

    def _txn_commit(self):
        # self.con.execute("COMMIT TRANSACTION")
	self.cur.close()
	self.con.commit()
	self.cur = None

    def _txn_abort(self):
	# self.con.execute("ROLLBACK")
	self.con.rollback()
	self.cur.close()
	self.cur = None

    # resetting db stats

    def _clearstats(self):
        pass

    #
    # methods for adding, updating and deleting songs
    #

    # helper methods

    def _queryregisterindex(self, table, indexnames, values):
	" register in table and return if tuple (id, newentry) "
	newindexentry = False
	wheres = " AND ".join(["%s = ?" % indexname for indexname in indexnames])
	self.cur.execute("SELECT id FROM %s WHERE %s" % (table, wheres), values)
	r = self.cur.fetchone()
	if r is None:
	    self.cur.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(indexnames),
								  ", ".join(["?"]*len(indexnames))), 
			     values)
	    self.cur.execute("SELECT id FROM %s WHERE %s" % (table, wheres), values)
	    r = self.cur.fetchone()
	    newindexentry = True
	return r["id"], newindexentry

    def _checkremoveindex(self, indextable, reftable, indexnames, value):
	"remove entry from indextable if no longer referenced in reftable and return whether this has happened"
	wheres = " OR ".join(["%s = ?" % indexname for indexname in indexnames])
	num = self.cur.execute("SELECT count(*) FROM %s WHERE (%s)" % (reftable, wheres),
			       [value]*len(indexnames)).fetchone()[0]
	if num == 0:
	    self.cur.execute("DELETE FROM %s WHERE id = ?" % indextable, [value])
	    return True
	else:
	    return False
	
    def _addsong(self, song):
        """add song to database"""
        log.debug("adding song: %r" % song)

        if not isinstance(song, dbitem.song):
            log.error("addsong: song has to be a dbitem.song instance, not a %r instance" % 
		      song.__class__)
            return

        self._txn_begin()
        try:
	    # query and register artist, album_artist and album
	    song.artist_id, newartist = self._queryregisterindex("artists", ["name"], [song.artist])
	    song.album_artist_id, newartist2 = self._queryregisterindex("artists", ["name"], 
									[song.album_artist])
	    newartist = newartist or newartist2

	    song.album_id, newalbum = self._queryregisterindex("albums", ["artist_id", "name"], 
							       [song.album_artist_id, song.album])
	    
	    # register song
            self.cur.execute("INSERT INTO songs (%s) VALUES (%s)" % (",".join(songcolumns),
								     ",".join(["?"] * len(songcolumns))),
			     [getattr(song, columnname) for columnname in songcolumns])

            self.cur.execute("SELECT id FROM songs WHERE url = ?", (song.url,))
            r = self.cur.fetchone()
            song_id = r["id"]

            # register song tags
            newtag = False
            for tag in song.tags:
                tag_id, newtag2 = self._queryregisterindex("tags", ["name"], [tag])
                newtag = newtag or newtag2
                self.cur.execute("INSERT INTO taggings (song_id, tag_id) VALUES (?, ?)", 
				 (song_id, tag_id))
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()
            if newartist:
                hub.notify(events.artistschanged(self.id))
            if newalbum:
                hub.notify(events.albumschanged(self.id))
            if newtag:
                hub.notify(events.tagschanged(self.id))
	    # we don't issue a songschanged event because the resulting queries put a too high load 
	    # on the database
	    # hub.notify(events.songschanged(self.id))

            #for r in cur.execute("SELECT id, name FROM artists"):
            #    log.info("AR: %s %s" % (r["id"], r["name"]))
            #for r in cur.execute("SELECT id, artist_id, name FROM albums"):
            #    log.info("AL: %s %s %s" % (r["id"], r["artist_id"], r["name"]))
            #for r in cur.execute("SELECT id, title FROM songs"):
            #    log.info("S: %s %s" % (r["id"], r["title"]))

    def _delsong(self, song):
        """delete song from database"""
        log.debug("delete song: %r" % song)
        if not isinstance(song, item.song):
            log.error("_delsong: song has to be a item.song instance, not a %r instance" % song.__class__)

        self._txn_begin()
        try:
	    # remove song
	    self.cur.execute("DELETE FROM songs WHERE id = ?", [song.id])

	    # remove corresponding album and artists
	    deletedalbum = self._checkremoveindex("albums", "songs", ["album_id"], song.album_id)
	    deletedartist = self._checkremoveindex("artists", "songs", ["album_artist_id", "artist_id"], 
						   song.artist_id)
	    deletedartist |= self._checkremoveindex("artists", "songs", ["album_artist_id", "artist_id"], 
						    song.album_artist_id)


	    # query tags in order to be able to delete them (as opposed to album_id, etc.,
	    # they are not stored in item.song)
	    tag_ids = []
	    for r in self.cur.execute("""SELECT DISTINCT tags.id AS tag_id FROM tags
	                                 JOIN taggings ON (taggings.tag_id =tags.id)
					 WHERE taggings.song_id = ?""", [song.id]):
		tag_ids.append(r["tag_id"])

	    # remove taggings
	    deletedtag = False
	    self.cur.execute("DELETE FROM taggings WHERE song_id = ?", [song.id])
	    for tag_id in tag_ids:
		deletedtag |= self._checkremoveindex("tags", "taggings", ["tag_id"], tag_id)
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()
            if deletedartist:
                hub.notify(events.artistschanged(self.id))
            if deletedalbum:
                hub.notify(events.albumschanged(self.id))
            if deletedtag:
                hub.notify(events.tagschanged(self.id))
	
	return
        # XXX send event?

    def _updatesong(self, oldsong, newsong):
        """updates entry of given song"""
        log.debug("updating song: %r" % song)
        if not isinstance(song, item.song):
            log.error("_updatesong: song has to be an item.song instance, not a %r instance" % song.__class__)
            return
        pass
        hub.notify(events.songchanged(self.id, song))

    def _registerplaylist(self, playlist):
        # also try to register songs in playlist and delete song, if
        # this fails
        paths = []
        for path in playlist.songs:
            try:
                if self._queryregistersong(path) is not None:
                    paths.append(path)
            except (IOError, OSError):
                pass
        playlist.songs = paths

        # a resulting, non-empty playlist can be written in the database
        if playlist.songs:
            self._txn_begin()
            try:
                self.playlists.put(playlist.path, playlist, txn=self.cur)
                hub.notify(events.dbplaylistchanged(self.id, playlist))
            except:
                self._txn_abort()
                raise
            else:
                self._txn_commit()

    def _delplaylist(self, playlist):
        """delete playlist from database"""
        if not self.playlists.has_key(playlist.id):
            raise KeyError

        log.debug("delete playlist: %r" % playlist)
        self._txn_begin()
        try:
            self.playlists.delete(playlist.id, txn=self.cur)
            hub.notify(events.dbplaylistchanged(self.id, playlist))
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()

    _updateplaylist = _registerplaylist

    # read-only methods for accesing the database

    ##########################################################################################
    # !!! It is not save to call any of the following methods when a transaction is active !!!
    ##########################################################################################

    def _getsong(self, song_id=None, song_url=None):
        """return song entry with given song_id or url"""
	if song_id is not None:
	    if song_url is not None:
		raise KeyError
	    wherestring = "WHERE songs.id = ?"
	    args = [song_id]
	elif song_url is not None:
	    wherestring = "WHERE songs.url = ?"
	    args = [song_url]
	else:
	    raise KeyError
        select = """SELECT %s, artists.name AS artist, albums.name AS album 
	            FROM songs 
                    JOIN albums ON albums.id == album_id
                    JOIN artists ON artists.id == songs.artist_id
                    %s
                    """ % (", ".join([c for c in songcolumns if c!="artist_id"]), wherestring)
        try:
            r = self.con.execute(select, args).fetchone()
            if r:
                # fetch tags
                tags = []
                select = """SELECT tags.name AS name FROM tags
                            JOIN taggings ON taggings.tag_id = tags.id
                            WHERE taggings.song_id = ?"""
                for tr in self.con.execute(select, (song_id,)):
                    tags.append(tr["name"])

		# fetch album artist
                select = """SELECT name FROM artists WHERE id = ?"""
                album_artist_name = self.con.execute(select, (r["album_artist_id"],)).fetchone()["name"]

                return dbitem.song(
                    r["url"], r["type"], r["title"], r["album"], r["artist"], album_artist_name, 
		    r["year"], r["comment"], 
                    r["lyrics"], tags,
                    r["tracknumber"], r["trackcount"], r["disknumber"], r["diskcount"], 
                    r["compilation"], r["length"], r["bitrate"],
                    r["samplerate"], r["is_vbr"], r["size"], r["replaygain_track_gain"], 
                    r["replaygain_track_peak"],
                    r["replaygain_album_gain"], r["replaygain_album_peak"],
                    r["date_added"], r["date_updated"], r["date_lastplayed"], 
                    r["playcount"], r["rating"])
            else:
                log.debug("Song '%r' not found in database" % args[0])
                return None
        except:
            log.debug_traceback()
            return None

    def _getalbum(self, album):
        """return given album"""
        return self.albums[album]

    def _getartist(self, artist):
        """return given artist"""
        return self.artists.get(artist)

    def _getsongs(self, filters=None):
        """ returns songs filtered according to filters"""
	joinstring = filters and filters.SQL_JOIN_string() or ""
	wherestring = filters and filters.SQL_WHERE_string() or ""
	args = filters and filters.SQLargs() or []
        select = """SELECT DISTINCT songs.id              AS song_id, 
	                            songs.album_id        AS album_id, 
				    songs.artist_id       AS artist_id,
				    songs.album_artist_id AS album_artist_id
                    FROM songs
                    JOIN artists  ON (songs.artist_id = artists.id)
                    JOIN albums   ON (songs.album_id = albums.id) 
		    %s
		    %s""" % (joinstring, wherestring)
	log.debug(select)
        return  [item.song(self.id, row["song_id"], row["album_id"], row["artist_id"], row["album_artist_id"])
		 for row in self.con.execute(select, args)]

    def _getartists(self, filters=None):
        """return artists filtered according to filters"""
	log.debug(filters.getname())
	joinstring = filters and filters.SQL_JOIN_string() or ""
	wherestring = filters and filters.SQL_WHERE_string() or ""
	args = filters and filters.SQLargs() or []
        select = """SELECT DISTINCT artists.id AS artist_id, artists.name AS artist_name
                    FROM artists 
		    JOIN songs    ON (songs.artist_id = artists.id)
		    JOIN albums   ON (album_id = albums.id)
		    %s
                    %s
                    ORDER BY artists.name COLLATE NOCASE""" % (joinstring, wherestring)
	log.debug(select)
        return [item.artist(self.id, row["artist_id"], row["artist_name"], filters)
                for row in self.con.execute(select, args)]

    def _getalbums(self, filters=None):
        """return albums filtered according to filters"""
	joinstring = filters and filters.SQL_JOIN_string() or ""
	wherestring = filters and filters.SQL_WHERE_string() or ""
	args = filters and filters.SQLargs() or []
	# Hackish, but effective to allow collections show up in artists view
	if filters.contains(item.artistfilter):
	    artist_id_column = "artist_id"
	else:
	    artist_id_column = "album_artist_id"
        select ="""SELECT DISTINCT albums.id AS album_id, artists.name AS artist_name, albums.name AS album_name
	           FROM albums 
		   JOIN artists  ON (songs.%s = artists.id)
		   JOIN songs    ON (songs.album_id = albums.id)
		   %s
		   %s
		   ORDER BY albums.name COLLATE NOCASE""" % (artist_id_column, joinstring, wherestring)

	log.debug(select)
        return [item.album(self.id, row["album_id"], row["artist_name"], row["album_name"], filters)
                for row in self.con.execute(select, args)]

    def _gettags(self, filters=None):
        """return tags filtered according to filters"""
	joinstring = filters and filters.SQL_JOIN_string() or ""
	wherestring = filters and filters.SQL_WHERE_string() or ""
	args = filters and filters.SQLargs() or []
        select ="""SELECT DISTINCT tags.id AS tag_id, tags.name AS tag_name
	           FROM tags
		   JOIN taggings ON (taggings.tag_id = tags.id)
		   JOIN songs ON (songs.id = taggings.song_id)
		   %s
		   %s
		   ORDER BY tags.name COLLATE NOCASE""" % (joinstring, wherestring)
	# JOIN taggings ON (taggings.tag_id = tags.id)
	log.debug(select)
        return [item.tag(self.id, row["tag_id"], row["tag_name"], filters)
                for row in self.con.execute(select, args)]

    def _getratings(self, filters):
        """return all stored ratings"""
	return []

    def _getlastplayedsongs(self, filters):
        """return the last played songs"""
        if not filters:
            return [(self.songs[songid], playingtime) for songid, playingtime in self.stats["lastplayed"]]
        else:
            songs = [self.songs[songid] for songid, playingtime in self.stats["lastplayed"]]
            filteredsongids = [song.id for song in self._filtersongs(songs, filters)]
            return [(self.songs[songid], playingtime) for songid, playingtime in self.stats["lastplayed"]
                    if songid in filteredsongids]

    def _gettopplayedsongs(self, filters):
        """return the top played songs"""
        keys = self.stats["topplayed"]
        return self._filtersongs(map(self.songs.get, keys), filters)

    def _getlastaddedsongs(self, filters):
        """return the last played songs"""
        keys = self.stats["lastadded"]
        return self._filtersongs(map(self.songs.get, keys), filters)

    def _getplaylist(self, path):
        """returns playlist entry with given path"""
        return self.playlists.get(path)

    def _getplaylists(self):
        return []
        return self.playlists.values()

    def _getsongsinplaylist(self, path):
        playlist = self._getplaylist(path)
        result = []
        for path in playlist.songs:
            try:
                song = self._queryregistersong(path)
                if song:
                    result.append(song)
            except IOError:
                pass
        return result

    def _getsongsinplaylists(self):
        playlists = self._getplaylists()
        songs = []
        for playlist in playlists:
            songs.extend(self._getsongsinplaylist(playlist.path))
        return songs

    def isbusy(self):
        """ check whether db is currently busy """
        return self.cur is not None or self.channel.queue.qsize()>0

    # event handlers

    def addsong(self, event):
        if event.songdbid == self.id:
            try:
                self._addsong(event.song)
            except KeyError:
		log.debug_traceback()
                pass

    def updatesong(self, event):
        if event.songdbid == self.id:
            try:
                self._updatesong(event.oldsong, event.newsong)
            except KeyError:
                pass

    def delsong(self, event):
        if event.songdbid == self.id:
            try:
                self._delsong(event.song)
            except:
		log.debug_traceback()
                pass

    def registerplaylists(self, event):
        if event.songdbid == self.id:
            for playlist in event.playlists:
                try: self._registerplaylist(playlist)
                except (IOError, OSError): pass
		
    def delplaylist(self, event):
        if event.songdbid == self.id:
            try:
                self._delplaylist(event.playlist)
            except KeyError:
                pass

    def updateplaylist(self, event):
        if event.songdbid == self.id:
            try:
                self._updateplaylist(event.playlist)
            except KeyError:
                pass

    def clearstats(self, event):
        if event.songdbid == self.id:
            self._clearstats()

    # request handlers

    def getdatabasestats(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return songdbstats(self.id, "local", self.basedir, None, self.dbfile, self.cachesize, 
			   0, 0, 0, 0)

    def getnumberofsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self.con.execute("SELECT count(*) FROM songs").fetchone()[0]

    def getnumberoftags(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self.con.execute("SELECT count(*) FROM tags").fetchone()[0]

    def getnumberofratings(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return 0

    def getnumberofalbums(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self.con.execute("SELECT count(*) FROM albums").fetchone()[0]

    def getnumberofartists(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self.con.execute("SELECT count(*) FROM artists").fetchone()[0]

    def getsong(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getsong(song_id=request.song_id, song_url=request.song_url)
        except KeyError:
            return None

    def getsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getsongs(request.filters)
        except (KeyError, AttributeError, TypeError):
            log.debug_traceback()
            return []

    def getartists(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getartists(request.filters)
        except KeyError:
            log.debug_traceback()
            return []

    def getartist(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getartist(request.artist)
        except KeyError:
            log.debug_traceback()
            return None

    def getalbums(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getalbums(request.filters)
        except KeyError:
            log.debug_traceback()
            return []

    def getalbum(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getalbum(request.album)
        except KeyError:
            log.debug_traceback()
            return None

    def gettags(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._gettags(request.filters)

    def getratings(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getratings(request.filters)

    def getlastplayedsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getlastplayedsongs(request.filters)

    def gettopplayedsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._gettopplayedsongs(request.filters)

    def getlastaddedsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getlastaddedsongs(request.filters)

    def getplaylist(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getplaylist(request.path)

    def getplaylists(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getplaylists()

    def getsongsinplaylist(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getsongsinplaylist(request.path)

    def getsongsinplaylists(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getsongsinplaylists()

#
# thread for automatic registering and rescanning of songs in database
#

class songautoregisterer(service.service):

    def __init__(self, basedir, songdbid, dbbusymethod,
                 tracknrandtitlere, tagcapitalize, tagstripleadingarticle, tagremoveaccents):
        service.service.__init__(self, "songautoregisterer", daemonize=True)
        self.basedir = basedir
        self.songdbid = songdbid
        self.dbbusymethod = dbbusymethod
        self.tracknrandtitlere = tracknrandtitlere
        self.tagcapitalize = tagcapitalize
        self.tagstripleadingarticle = tagstripleadingarticle
        self.tagremoveaccents = tagremoveaccents
        self.done = False
        # support file extensions
        self.supportedextensions = metadata.getextensions()

        self.channel.subscribe(events.autoregistersongs, self.autoregistersongs)
        self.channel.subscribe(events.rescansongs, self.rescansongs)

    def _notify(self, event):
        """ wait until db is not busy and send event """
        while self.dbbusymethod():
            time.sleep(0.1)
        hub.notify(event, -100)

    def _request(self, request):
        """ wait until db is not busy and send event """
        while self.dbbusymethod():
            time.sleep(0.1)
	return hub.request(request, -100)

    def registerdirtree(self, dir, oldsongs, force):
        """ scan for songs and playlists in dir and its subdirectories, 
	removing those scanned from the set oldsongs. If force is set, 
	the m_time of a song is ignored and the song is always scanned.
	"""
        log.debug("registerer: entering %r"% dir)
        self.channel.process()
        if self.done: return
        songpaths = []
        playlistpaths = []

        # scan for paths of songs and playlists and recursively call registering of subdirectories
        for name in os.listdir(dir):
            path = os.path.join(dir, name)
            extension = os.path.splitext(path)[1].lower()
            if os.access(path, os.R_OK):
                if os.path.isdir(path):
                    try:
                        self.registerdirtree(path, oldsongs, force)
                    except (IOError, OSError), e:
                        log.warning("songautoregisterer: could not enter dir %r: %r" % (path, e))
                elif extension in self.supportedextensions:
                    songpaths.append(path)
                elif extension == ".m3u":
                    playlistpaths.append(path)

        # now register songs...
        songs = []
        for path in songpaths:
	    # generate url corresponding to song
            if self.basedir.endswith("/"):
               relpath = path[len(self.basedir):]
            else:
               relpath = path[len(self.basedir)+1:]

	    song_url = "file://" + relpath
	    song = self._request(requests.getsong(self.songdbid, song_url=song_url))
	    
	    if song:
		if force or song.date_updated < os.stat(path).st_mtime:
		    # the song has changed since the last update
		    newsong = dbitem.songfromfile(relpath, self.basedir,
						  self.tracknrandtitlere,
						  self.tagcapitalize, self.tagstripleadingarticle, 
						  self.tagremoveaccents)
		    assert newsong.url == song.url, RuntimeError("song urls changed")
		else:
		    log.debug("registerer: not scanning unchanged song '%r'" % song_url)

		# remove song from list of songs to be checked
		oldsongs.discard(song)
	    else:
		# song was not stored in database
		newsong = dbitem.songfromfile(relpath, self.basedir,
					      self.tracknrandtitlere,
					      self.tagcapitalize, self.tagstripleadingarticle, 
					      self.tagremoveaccents)
		self._notify(events.addsong(self.songdbid, newsong))

        # ... and playlists
	# XXX to be done
        playlists = [dbitem.playlist(path) for path in playlistpaths]
        if playlists:
            self._notify(events.registerplaylists(self.songdbid, playlists))

        log.debug("registerer: leaving %r"% dir)

    def run(self):
        # wait a little bit to not disturb the startup too much
        time.sleep(2)
        service.service.run(self)

    def rescansong(self, song):
	# XXX to be done
        # to take load of the database thread, we also enable the songautoregisterer
        # to rescan songs
        try:
            song.scanfile(self.basedir,
                          self.tracknrandtitlere,
                          self.tagcapitalize, self.tagstripleadingarticle, self.tagremoveaccents)
            self._notify(events.updatesong(self.songdbid, song))
        except IOError:
            self._notify(events.delsong(self.songdbid, song))

    def rescanplaylist(self, playlist):
        try:
            newplaylist = dbitem.playlist(playlist.path)
            self._notify(events.updateplaylist(self.songdbid, newplaylist))
        except IOError:
            self._notify(events.delplaylist(self.songdbid, playlist))

    #
    # event handler
    #

    def autoregistersongs(self, event):
        if self.songdbid == event.songdbid:
            log.info(_("database %r: scanning for songs and playlists in %r") % (self.songdbid, 
										 self.basedir))

            log.debug("database %r: querying list of songs in database" % self.songdbid)
            oldsongs = set(hub.request(requests.getsongs(self.songdbid)))
            #for song in oldsongs: self._notify(events.delsong(self.songdbid, song))
	    #return

            # scan for all songs and playlists in the filesystem
            log.debug("database %r: searching for new songs" % self.songdbid)
            self.registerdirtree(self.basedir, oldsongs, event.force)

            # remove songs which have not yet been scanned and thus are not
	    # accesible anymore
            log.debug("database %r: removing stale songs" % self.songdbid)
            for song in oldsongs:
		self._notify(events.delsong(self.songdbid, song))

	    # Note that we don't remove old playlists when they are no longer available on disk

            log.info(_("database %r: finished scanning for songs in %r") % (self.songdbid, self.basedir))

    def rescansongs(self, event):
        if self.songdbid == event.songdbid:
            log.info(_("database %r: rescanning %d songs") % (self.songdbid, len(event.songs)))
            for song in event.songs:
                self.rescansong(song)
            log.info(_("database %r: finished rescanning %d songs") % (self.songdbid, len(event.songs)))

