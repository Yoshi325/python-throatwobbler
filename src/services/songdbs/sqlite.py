
# -*- coding: ISO-8859-1 -*-

# Copyright (C) 2002, 2003, 2004, 2006 Jörg Lehmann <joerg@luga.de>
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
  date_changed          TIMESTAMP,
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
"""

songcolumns = ["url", "type", "title", "album_id",
               "artist_id", "album_artist_id", "year", "comment", "lyrics",
               "length", "tracknumber", "trackcount", "disknumber", "diskcount",
               "compilation", "bitrate", "is_vbr", "samplerate", 
               "replaygain_track_gain", "replaygain_track_peak",
               "replaygain_album_gain", "replaygain_album_peak", 
               "size", "compilation", "date_added", "date_changed", "date_lastplayed", 
               "playcount", "rating"]


#
# statistical information about songdb
#

class songdbstats:
    def __init__(self, id, type, basedir, location, dbenvdir, cachesize,
                 numberofsongs, numberofalbums, numberofartists, numberofgenres, numberofdecades):
        self.id = id
        self.type = type
        self.basedir = basedir
        self.location = location
        self.dbenvdir = dbenvdir
        self.cachesize = cachesize
        self.numberofsongs = numberofsongs
        self.numberofalbums = numberofalbums
        self.numberofartists = numberofartists
        self.numberofgenres = numberofgenres
        self.numberofdecades = numberofdecades

#
# songdb class
#

class songdb(service.service):
    def __init__(self, id, config, songdbhub):
        service.service.__init__(self, "%r songdb" % id, hub=songdbhub)
        self.id = id
        self.songdbbase = config.basename
        self.dbfile = "sqlite.db"
        self.basedir = config.musicbasedir

        self.playingstatslength = config.playingstatslength
        self.tracknrandtitlere = config.tracknrandtitlere
        self.tagcapitalize = config.tags_capitalize
        self.tagstripleadingarticle = config.tags_stripleadingarticle
        self.tagremoveaccents = config.tags_removeaccents
        # unneeded
        # self.dbenvdir = config.dbenvdir
        self.cachesize = config.cachesize

        if not os.path.isdir(self.basedir):
            raise errors.configurationerror("musicbasedir '%r' of database %r is not a directory." % (self.basedir, self.id))

        if not os.access(self.basedir, os.X_OK | os.R_OK):
            raise errors.configurationerror("you are not allowed to access and read config.general.musicbasedir.")


        # currently active transaction - initially, none
        self.txn = None

        try:
            self._initdb()
        except:
            raise errors.databaseerror("cannot initialise/open song database files.")

        # we need to be informed about database changes
        self.channel.subscribe(events.updatesong, self.updatesong)
        self.channel.subscribe(events.rescansong, self.rescansong)
        self.channel.subscribe(events.delsong, self.delsong)
        self.channel.subscribe(events.updateplaylist, self.updateplaylist)
        self.channel.subscribe(events.delplaylist, self.delplaylist)
        self.channel.subscribe(events.updatealbum, self.updatealbum)
        self.channel.subscribe(events.updateartist, self.updateartist)
        self.channel.subscribe(events.registersongs, self.registersongs)
        self.channel.subscribe(events.registerplaylists, self.registerplaylists)
        self.channel.subscribe(events.clearstats, self.clearstats)

        # we are a database service provider...
        self.channel.supply(requests.getdatabasestats, self.getdatabasestats)
        self.channel.supply(requests.queryregistersong, self.queryregistersong)
        self.channel.supply(requests.getartists, self.getartists)
        self.channel.supply(requests.getalbums, self.getalbums)
        self.channel.supply(requests.getalbum, self.getalbum)
        self.channel.supply(requests.getartist, self.getartist)
        self.channel.supply(requests.getsong, self.getsong)
        self.channel.supply(requests.getsongs, self.getsongs)
        self.channel.supply(requests.getnumberofsongs, self.getnumberofsongs)
        self.channel.supply(requests.getnumberofalbums, self.getnumberofalbums)
        self.channel.supply(requests.getnumberofartists, self.getnumberofartists)
        self.channel.supply(requests.getnumberofgenres, self.getnumberofgenres)
        self.channel.supply(requests.getnumberofdecades, self.getnumberofdecades)
        self.channel.supply(requests.getnumberofratings, self.getnumberofratings)
        self.channel.supply(requests.getgenres, self.getgenres)
        self.channel.supply(requests.getdecades, self.getdecades)
        self.channel.supply(requests.getratings, self.getratings)
        self.channel.supply(requests.getlastplayedsongs, self.getlastplayedsongs)
        self.channel.supply(requests.gettopplayedsongs, self.gettopplayedsongs)
        self.channel.supply(requests.getlastaddedsongs, self.getlastaddedsongs)
        self.channel.supply(requests.getplaylist, self.getplaylist)
        self.channel.supply(requests.getplaylists, self.getplaylists)
        self.channel.supply(requests.getsongsinplaylist, self.getsongsinplaylist)
        self.channel.supply(requests.getsongsinplaylists, self.getsongsinplaylists)

        self.autoregisterer = songautoregisterer(self.basedir, self.id, self.isbusy,
                                                 self.tracknrandtitlere,
                                                 self.tagcapitalize, self.tagstripleadingarticle, self.tagremoveaccents)
        self.autoregisterer.start()

    def _initdb(self):
        """ initialise sqlite database """

        #log.info(_("database %r: basedir %r, %d songs, %d artists, %d albums, %d genres, %d playlists") %
        #         (self.id, self.basedir, len(self.songs),  len(self.artists),  len(self.albums),
        #          len(self.genres), len(self.playlists)))

    def run(self):
        self.con = sqlite.connect(":memory:")
        self.con.row_factory = sqlite.Row
        self.con.executescript(create_tables)
        service.service.run(self)
        self.close()

    def close(self):
        self.con.close()

    # transaction machinery

    def _txn_begin(self):
        return
        if self.txn:
            raise RuntimeError("more than one transaction in parallel is not supported")
        self.txn = self.dbenv.txn_begin()

    def _txn_commit(self):
        return
        self.txn.commit()
        self.txn = None

    def _txn_abort(self):
        return
        self.txn.abort()
        self.txn = None

    # resetting db stats

    def _clearstats(self):
        pass

    # methods for registering, deleting and updating of song database

    def _queryregistersong(self, path):
        """get song info from database or insert new one"""
        log.debug("querying song: %r" % path)

        path = os.path.normpath(path)

        # check if we are allowed to store this song in this database
        if not path.startswith(self.basedir):
            log.error("_queryregistersong: song path has to be located in basedir")
            return None

        # we assume that the relative (with respect to the basedir)
        # path of the song is the song id.  This allows us to quickly
        # verify (without reading the song itself) whether we have
        # already registered the song. Otherwise, we would have to
        # create a song instance, which is quite costly.
        if self.basedir.endswith("/"):
           relpath = path[len(self.basedir):]
        else:
           relpath = path[len(self.basedir)+1:]
        self.con.execute("SELECT id FROM songs WHERE url = ?", ("file://" + relpath,)).fetchone()
        if r:
            return self._getsong(r["id"])
        else:
            song = dbitem.songfromfile(relpath, self.basedir,
                                       self.tracknrandtitlere, self.tagcapitalize, 
                                       self.tagstripleadingarticle, self.tagremoveaccents)

            self._registersong(song)
            return song
        # XXX send event?

    def _delsong(self, song):
        """delete song from database"""
        log.debug("delete song: %r" % song)
        if not isinstance(song, dbitem.song):
            log.error("_delsong: song has to be a dbitem.song instance, not a %r instance" % song.__class__)
            return
        # XXX send event?

    def _updatesong(self, song):
        """updates entry of given song"""
        log.debug("updating song: %r" % song)
        if not isinstance(song, dbitem.song):
            log.error("_updatesong: song has to be a dbitem.song instance, not a %r instance" % song.__class__)
            return
        pass
        hub.notify(events.songchanged(self.id, song))

    def _registersong(self, song):
        """add song to database"""
        log.debug("registering song: %r" % song)

        if not isinstance(song, dbitem.song):
            log.error("updatesong: song has to be a dbitem.song instance, not a %r instance" % song.__class__)
            return
        #if not song.path.startswith(self.basedir):
        #    log.error("registersong: song path has to be located in basedir")
        #    return

        #try:
        #    newsong = self._getsong(song.id)
        #    # if the song is already in the database, we just update
        #    # its id3 information (in case that it changed) and
        #    # write the new song in the database
        #    newsong.update_id3(song)
        #     self._updatesong(newsong)
        #except:
        self._txn_begin()
        cur = self.con.cursor()
        def queryregisterindex(indextable, name):
            newindexentry = False
            cur.execute("SELECT id FROM %s WHERE name=?" % indextable, (name, ))
            r = cur.fetchone()
            if r is None:
                cur.execute("INSERT INTO %s (name) VALUES (?)" % indextable, (name, ))
                cur.execute("SELECT id FROM %s WHERE name=?" % indextable, (name,))
                r = cur.fetchone()
                newindexentry = True
            return r["id"], newindexentry
        try:
	    song.artist_id, newartist = queryregisterindex("artists", song.artist)
	    song.album_artist_id, newartist2 = queryregisterindex("artists", song.album_artist)
	    newartist = newartist or newartist2

            newalbum = False
            cur.execute("SELECT id FROM albums WHERE artist_id=? AND name=?", 
                        (song.album_artist_id, song.album))
            r = cur.fetchone()
            if r is None:
                cur.execute("INSERT INTO albums (artist_id, name) VALUES (?, ?)", 
                            (song.album_artist_id, song.album))
                cur.execute("SELECT id FROM albums WHERE artist_id=? AND name=?", 
                            (song.album_artist_id, song.album))
                r = cur.fetchone()
                newalbum = True
            song.album_id = r["id"]

            cur.execute("INSERT INTO songs (%s) VALUES (%s)" % (",".join(songcolumns),
                                                                ",".join(["?"] * len(songcolumns))),
                        [getattr(song, columnname) for columnname in songcolumns])

            cur.execute("SELECT id FROM songs WHERE url = ?", (song.url,))
            r = cur.fetchone()
            song_id = r["id"]

            # register song tags
            newtag = False
            for tag in song.tags:
                tag_id, newtag2 = queryregisterindex("tags", tag)
                newtag = newtag or newtag2
                cur.execute("INSERT INTO taggings (song_id, tag_id) VALUES (?, ?)", (song_id, tag_id))
            self.con.commit()

            #for r in cur.execute("SELECT id, name FROM artists"):
            #    log.info("AR: %s %s" % (r["id"], r["name"]))
            #for r in cur.execute("SELECT id, artist_id, name FROM albums"):
            #    log.info("AL: %s %s %s" % (r["id"], r["artist_id"], r["name"]))
            #for r in cur.execute("SELECT id, title FROM songs"):
            #    log.info("S: %s %s" % (r["id"], r["title"]))

            if newartist:
                hub.notify(events.artistschanged(self.id))
            if newalbum:
                hub.notify(events.albumschanged(self.id))
            if newtag:
                hub.notify(events.tagschanged(self.id))
            # hub.notify(events.songchanged(self.id, song))
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()


#    def _rescansong(self, song):
#        """reread id3 information of song (or delete it if it does not longer exist)"""
#        try:
#            song.scanfile(self.basedir,
#                          self.tracknrandtitlere,
#                          self.tagcapitalize, self.tagstripleadingarticle, self.tagremoveaccents)
#            self._updatesong(song)
#        except IOError:
#            self._delsong(song)

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
                self.playlists.put(playlist.path, playlist, txn=self.txn)
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
            self.playlists.delete(playlist.id, txn=self.txn)
            hub.notify(events.dbplaylistchanged(self.id, playlist))
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()

    _updateplaylist = _registerplaylist

    def _updatealbum(self, album):
        """updates entry of given album of artist"""
        # XXX: changes of other indices not handled correctly
        self._txn_begin()
        try:
            self.albums.put(album.id, album, txn=self.txn)
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()

    def _updateartist(self, artist):
        """updates entry of given artist"""
        # XXX: changes of other indices not handled correctly

        self._txn_begin()
        try:
            # update artist cache if existent
            self.artists.put(artist.name, artist, txn=self.txn)
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()

    # read-only methods for accesing the database

    ##########################################################################################
    # !!! It is not save to call any of the following methods when a transaction is active !!!
    ##########################################################################################

    def _getsong(self, song_id):
        """return song entry with given song_id"""
        select = """SELECT %s, artists.name AS artist, albums.name AS album 
	            FROM songs 
                    JOIN albums ON albums.id == album_id
                    JOIN artists ON artists.id == songs.artist_id
                    WHERE songs.id = ?
                    """ % ", ".join([c for c in songcolumns if c!="artist_id"])
        try:
            r = self.con.execute(select, (song_id,)).fetchone()
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
                    r["date_added"], r["date_changed"], r["date_lastplayed"], 
                    r["playcount"], r["rating"])
            else:
                log.debug("Song '%d' not found in database" % song_id)
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
        """ returns song  filtered according ot filters"""
	filterstring = filters and filters.SQLstring() or ""
	filterargs = filters and filters.SQLargs() or []
        select = """SELECT songs.id as song_id
                    FROM songs
                    JOIN artists ON (songs.artist_id = artists.id)
                    JOIN albums  ON (songs.album_id = albums.id) 
		    %s""" % filterstring
	log.debug(select)
        return  [item.song(self.id, row["song_id"])
		 for row in self.con.execute(select, filterargs)]

    def _getartists(self, filters=None):
        """return all stored artists"""
	filterstring = filters and filters.SQLstring() or ""
	filterargs = filters and filters.SQLargs() or []
        select = """SELECT DISTINCT artists.id AS artist_id, artists.name AS artist_name
                    FROM artists JOIN songs ON (artist_id = artists.id)
                    %s
                    ORDER BY artists.name COLLATE NOCASE""" % filterstring
        return [item.artist(self.id, row["artist_id"], row["artist_name"], filters)
                for row in self.con.execute(select, filterargs)]

    def _getalbums(self, filters=None):
        """return albums of a given artist

        artist_name has to be a string. If it is None, all stored
        albums are returned
        """
	filterstring = filters and filters.SQLstring() or ""
	filterargs = filters and filters.SQLargs() or []
        select =""" SELECT DISTINCT albums.id AS album_id, artists.name AS artist_name, albums.name AS album_name
                    FROM albums 
		    JOIN artists ON (albums.artist_id = artists.id)
		    JOIN songs ON (songs.album_id = albums.id)
		    %s
		    ORDER BY albums.name COLLATE NOCASE""" % filterstring
	log.debug(select)
        return [item.album(self.id, row["album_id"], row["artist_name"], row["album_name"], filters)
                for row in self.con.execute(select, filterargs)]

    def _filterindex(self, index, filters):
        """ return all keys in index filtered by filters """
        items = getattr(self, index).values()
        if filters:
            for filter in filters:
                newitems = []
                indexname = filter.indexname
                indexid = filter.indexid
                for item in items:
                    for song in map(self.songs.get, item.songs):
                        if getattr(song, indexname) == indexid:
                            newitems.append(item)
                            break
                items = newitems
        return items

    def _getratings(self, filters):
        """return all stored ratings"""
        return self._filterindex("ratings", filters)

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
        return self.txn is not None or self.channel.queue.qsize()>0

    # event handlers

    def updatesong(self, event):
        if event.songdbid == self.id:
            try:
                self._updatesong(event.song)
            except KeyError:
                pass

    def rescansong(self, event):
        log.error("rescansong obsolete")
        return
        if event.songdbid == self.id:
            try:
                self._rescansong(event.song)
            except KeyError:
                pass

    def delsong(self, event):
        if event.songdbid == self.id:
            try:
                self._delsong(event.song)
            except KeyError:
                pass

    def updatealbum(self, event):
        if event.songdbid == self.id:
            try:
                self._updatealbum(event.album)
            except KeyError:
                pass

    def updateartist(self, event):
        if event.songdbid == self.id:
            try:
                self._updateartist(event.artist)
            except KeyError:
                pass

    def registersongs(self, event):
        if event.songdbid == self.id:
            for song in event.songs:
                try: self._registersong(song)
                except (IOError, OSError): pass

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
        numberofdecades = self.getnumberofdecades(requests.getnumberofdecades(self.id))
        return songdbstats(self.id, "local", self.basedir, None, "", self.cachesize, 0, 0, 0, 0, 0)

    def getnumberofsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self.con.execute("SELECT count(*) FROM songs").fetchone()[0]

    def getnumberofdecades(self, request):
        return 0
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return len(self.decades.keys())

    def getnumberofgenres(self, request):
        return 0
        if self.id != request.songdbid:
            raise hub.DenyRequest
        # XXX why does len(self.genres) not work???
        # return len(self.genres)
        return len(self.genres.keys())

    def getnumberofratings(self, request):
        return 0
        if self.id != request.songdbid:
            raise hub.DenyRequest
        # XXX why does len(self.genres) not work???
        # return len(self.genres)
        return len(self.ratings.keys())

    def getnumberofalbums(self, request):
	return 0
        if self.id != request.songdbid:
            raise hub.DenyRequest
        # see above
        return len(self.albums.keys())

    def getnumberofartists(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        # see above
        return len(self.artists.keys())

    def queryregistersong(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._queryregistersong(request.path)

    def getsong(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getsong(request.id)
        except KeyError:
            log.debug_traceback()
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

    def getgenres(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getgenres(request.filters)

    def getdecades(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return self._getdecades(request.filters)

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

    def registerdirtree(self, dir):
        """ scan for songs and playlists in dir and its subdirectories, returning all items which have been scanned """
        log.debug("registerer: entering %r"% dir)
        self.channel.process()
        if self.done: return []
        songpaths = []
        playlistpaths = []
        registereditems = []
        # number of songs sent to the database at one time
        dividesongsby = 5

        # scan for paths of songs and playlists and recursively call registering of subdirectories
        for name in os.listdir(dir):
            path = os.path.join(dir, name)
            extension = os.path.splitext(path)[1].lower()
            if os.access(path, os.R_OK):
                if os.path.isdir(path):
                    try:
                        registereditems.extend(self.registerdirtree(path))
                    except (IOError, OSError), e:
                        log.warning("songautoregisterer: could not enter dir %r: %r" % (path, e))
                elif extension in self.supportedextensions:
                    songpaths.append(path)
                elif extension == ".m3u":
                    playlistpaths.append(path)

        # now register songs...
        songs = []
        for path in songpaths:
            if self.basedir.endswith("/"):
               songid = path[len(self.basedir):]
            else:
               songid = path[len(self.basedir)+1:]
            songs.append(dbitem.songfromfile(songid, self.basedir,
                                             self.tracknrandtitlere,
                                             self.tagcapitalize, self.tagstripleadingarticle, self.tagremoveaccents))
        if songs:
            for i in xrange(0, len(songs), dividesongsby):
                self._notify(events.registersongs(self.songdbid, songs[i:i+dividesongsby]))
            registereditems.extend(songs)

        # ... and playlists
        playlists = [dbitem.playlist(path) for path in playlistpaths]
        if playlists:
            self._notify(events.registerplaylists(self.songdbid, playlists))

        registereditems.extend(playlists)
        log.debug("registerer: leaving %r"% dir)
        return registereditems

    def run(self):
        # wait a little bit to not disturb the startup too much
        time.sleep(2)
        service.service.run(self)

    def rescansong(self, song):
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
            log.info(_("database %r: scanning for songs in %r") % (self.songdbid, self.basedir))

            # get all songs and playlists currently stored in the database
            oldsongs = hub.request(requests.getsongs(self.songdbid))
            oldplaylists = hub.request(requests.getplaylists(self.songdbid))

            # scan for all songs and playlists in the filesystem
            log.debug("database %r: searching for new songs" % self.songdbid)
            registereditems = self.registerdirtree(self.basedir)

            # update information for songs which have not yet been scanned (in particular
            # remove songs which are no longer present in the database)
            log.debug("database %r: removing stale songs" % self.songdbid)
            registereditemshash = {}
            for item in registereditems:
                registereditemshash[item] = None
            for song in oldsongs:
                if song not in registereditemshash:
                    self.rescansong(song)
            for playlist in oldplaylists:
                if playlist not in registereditemshash:
                    self.rescanplaylist(playlist)

            log.info(_("database %r: finished scanning for songs in %r") % (self.songdbid, self.basedir))

    def rescansongs(self, event):
        if self.songdbid == event.songdbid:
            log.info(_("database %r: rescanning %d songs") % (self.songdbid, len(event.songs)))
            for song in event.songs:
                self.rescansong(song)
            log.info(_("database %r: finished rescanning %d songs") % (self.songdbid, len(event.songs)))

