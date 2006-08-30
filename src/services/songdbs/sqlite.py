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
import service


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

# interval in seconds after which logs are flushed
checkpointinterval = 60

class songdb(service.service):
    def __init__(self, id, config, songdbhub):
        service.service.__init__(self, "%s songdb" % id, hub=songdbhub)
        self.id = id
        self.songdbbase = config.basename
        self.dbfile = "db"
        self.basedir = config.musicbasedir
        self.playingstatslength = config.playingstatslength
        self.tracknrandtitlere = config.tracknrandtitlere
        self.tagcapitalize = config.tags_capitalize
        self.tagstripleadingarticle = config.tags_stripleadingarticle
        self.tagremoveaccents = config.tags_removeaccents
        self.dbenvdir = config.dbenvdir
        self.cachesize = config.cachesize

        if not os.path.isdir(self.basedir):
            raise errors.configurationerror("musicbasedir '%s' of database %s is not a directory." % (self.basedir, self.id))

        if not os.access(self.basedir, os.X_OK | os.R_OK):
            raise errors.configurationerror("you are not allowed to access and read config.general.musicbasedir.")

        self.dbenv = dbenv(self.dbenvdir, self.cachesize)

        # We keep the year index still around although we do not use it anymore.
        # otherwise we run into troubles when upgrading from the old mulit-file layout
        self.indices = ["genre", "decade", "rating"]

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

        # regularly flush the database log
        self.channel.subscribe(events.checkpointdb, self.checkpointdb)
        # send this event to normal hub, since otherwise the timer service does not get it
        hub.notify(events.sendeventin(events.checkpointdb(self.id), checkpointinterval, repeat=checkpointinterval))

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
        """ initialise database using modern bsddb interface of Python 2.3 and above """

        openflags = bsddb.db.DB_CREATE

        self.songs = self.dbenv.openshelve(self.dbfile, flags=openflags, dbname="songs")
        self.artists = self.dbenv.openshelve(self.dbfile, flags=openflags, dbname="artists")
        self.albums = self.dbenv.openshelve(self.dbfile, flags=openflags, dbname="albums")
        self.playlists = self.dbenv.openshelve(self.dbfile, flags=openflags, dbname="playlists")
        for index in self.indices:
            setattr(self, index+"s", self.dbenv.openshelve(self.dbfile, flags=openflags, dbname=index+"s"))
        self.stats = self.dbenv.openshelve(self.dbfile, flags=openflags, dbname="stats")

        # check whether we have to convert from an old multi-file database layout
        if self.songdbbase:
            songdbprefix = self.songdbbase
            if os.path.exists(songdbprefix + "_CONVERTED"):
                log.warning(_('using new database, please set "basename=" in [database.%s] section of your config file') % self.id)
            else:
                self._convertfromoldfilelayout()

        log.info(_("database %s: basedir %s, %d songs, %d artists, %d albums, %d genres, %d playlists") %
                 (self.id, self.basedir, len(self.songs),  len(self.artists),  len(self.albums),
                  len(self.genres), len(self.playlists)))

    def run(self):
        service.service.run(self)
        self.close()

    def close(self):
        self.dbenv.close()

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

    def _checkpoint(self):
        """flush memory pool, write checkpoint record to log and flush flog"""
        pass

    # resetting db stats

    def _clearstats(self):
        pass

    # methods for registering, deleting and updating of song database

    def _queryregistersong(self, path):
        """get song info from database or insert new one"""

        path = os.path.normpath(path)

        # check if we are allowed to store this song in this database
        if not path.startswith(self.basedir):
            return None

        # we assume that the relative (with respect to the basedir)
        # path of the song is the song id.  This allows to quickly
        # verify (without reading the song itself) whether we have
        # already registered the song. Otherwise, we would have to
        # create a song instance, which is quite costly.
        if self.basedir.endswith("/"):
           song_id = path[len(self.basedir):]
        else:
           song_id = path[len(self.basedir)+1:]
        try:
            song = self.songs[song_id]
        except KeyError:
            song = dbitem.song(song_id, self.basedir, self.tracknrandtitlere, self.tagcapitalize, self.tagstripleadingarticle, self.tagremoveaccents)

            self._txn_begin()
            try:
                self.songs.put(song.id, song, txn=self.txn)
                # insert into indices
                self._indexsong(song)
            except:
                self._txn_abort()
                raise
            else:
                self._txn_commit()
                log.debug("new song %s" % path)

        return song

    def _delsong(self, song):
        """delete song from database"""
        if not self.songs.has_key(song.id):
            raise KeyError

        log.debug("delete song: %s" % str(song))
        self._txn_begin()
        try:
            self._unindexsong(song)
            self.songs.delete(song.id, txn=self.txn)
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()

    def _updatesong(self, song):
        """updates entry of given song"""

        if not isinstance(song, dbitem.song):
            log.error("updatesong: song has to be a dbitem.song instance, not a %s instance" % repr(song.__class__))
            return

        self._txn_begin()
        try:
            oldsong = self.songs.get(song.id, txn=self.txn)
            self.songs.put(song.id, song, txn=self.txn)
            self._reindexsong(oldsong, song)
        except:
            self._txn_abort()
            raise
        else:
            self._txn_commit()
        hub.notify(events.songchanged(self.id, song))

    def _registersong(self, song):
        """register song into database or rescan existent one"""

        # check if we are allowed to store this song in this database
        if not song.path.startswith(self.basedir):
            return

        if song.id in self.songs:
            # if the song is already in the database, we just update
            # its id3 information (in case that it changed) and
            # write the new song in the database
            newsong = self.songs[song.id]
            newsong.update(song)
            self._updatesong(newsong)
        else:
            self._txn_begin()
            try:
                self.songs.put(song.id, song, txn=self.txn)
                # insert into indices
                self._indexsong(song)
            except:
                self._txn_abort()
                raise
            else:
                self._txn_commit()

    def _rescansong(self, song):
        """reread id3 information of song (or delete it if it does not longer exist)"""
        try:
            song.scanfile(self.basedir,
                          self.tracknrandtitlere,
                          self.tagcapitalize, self.tagstripleadingarticle, self.tagremoveaccents)
            self._updatesong(song)
        except IOError:
            self._delsong(song)

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

        log.debug("delete playlist: %s" % str(playlist))
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

    def _getsong(self, id):
        """return song entry with given id"""
        song = self.songs.get(id)
        return song

    def _getalbum(self, album):
        """return given album"""
        return self.albums[album]

    def _getartist(self, artist):
        """return given artist"""
        return self.artists.get(artist)

    def _filtersongs(self, songs, filters):
        """return items matching filters"""
        for filter in filters:
            indexname = filter.indexname
            indexid = filter.indexid
            songs = [song for song in songs if getattr(song, indexname) == indexid]
        return songs

    def _getsongs(self, artist=None, album=None, filters=None):
        """ returns song of given artist, album and with song.indexname==indexid

        All values either have to be strings or None, in which case they are ignored.
        """

        if artist is None and album is None and not filters:
            # return all songs in songdb
            # songs = map(self.songs.get, self.songs.keys())
            return self.songs.values()

        if not filters:
            if album is None:
                # return all songs of a given artist
                keys = self._getartist(artist).songs
                songs = map(self.songs.get, keys)
                return songs
            elif artist is None:
                keys = self._getalbum(album).songs
                return map(self.songs.get, keys)
            else:
                # return all songs on a given album of a given artist
                # We first determine all songs of the artist and filter afterwards
                # for the songs on the given album. Doing it the other way round,
                # turns out to be really bad for the special case of an unknown
                # album which contains songs of many artists.
                keys = self._getartist(artist).songs
                songs = map(self.songs.get, keys)
                return [song for song in songs if song.album==album]
        else:
            # filters specified
            if artist is None and album is None:
                index = getattr(self, filters[0].indexname+"s")
                songs = map(self.songs.get, index[str(filters[0].indexid)].songs)
                return self._filtersongs(songs, filters[1:])
            else:
                songs = self._getsongs(artist=artist, album=album)
                return self._filtersongs(songs, filters)

    def _filteralbumartists(self, itemname, filters, itemids=None):
        itemgetter = getattr(self, itemname).get
        # consider case without filters separately
        if not filters:
           if itemids is None:
               itemids = getattr(self, itemname).keys()
           return map(itemgetter, itemids)

        if itemids is None:
            index = getattr(self, filters[0].indexname+"s")
            itemids = getattr(index[str(filters[0].indexid)], itemname)
            filters = filters[1:]
        # we use a hash to construction of the intersection of the results of the various filters
        items = {}
        for itemid in itemids:
            items[itemid] = itemgetter(itemid)

        for filter in filters:
            newitems = {}
            index = getattr(self, filter.indexname+"s")
            for itemid in getattr(index[str(filter.indexid)], itemname):
                if itemid in items:
                    newitems[itemid] = items[itemid]
            items = newitems
        return items.values()

    def _getartists(self, filters=None):
        """return all stored artists"""
        return self._filteralbumartists("artists", filters)

    def _getalbums(self, artist=None, filters=None):
        """return albums of a given artist and genre

        artist has to be a string. If it is none, all stored
        albums are returned
        """
        if artist is None:
            return self._filteralbumartists("albums", filters)
        else:
            return self._filteralbumartists("albums", filters, self.artists[artist].albums)

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

    def _getgenres(self, filters):
        """return all stored genres"""
        return self._filterindex("genres", filters)

    def _getdecades(self, filters):
        """return all stored decades"""
        return self._filterindex("decades", filters)

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

    def checkpointdb(self, event):
        """flush memory pool, write checkpoint record to log and flush flog"""
        if event.songdbid == self.id:
            self._checkpoint()

    def updatesong(self, event):
        if event.songdbid == self.id:
            try:
                self._updatesong(event.song)
            except KeyError:
                pass

    def rescansong(self, event):
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
        return songdbstats(self.id, "local", self.basedir, None, self.dbenvdir, self.cachesize,
                           len(self.songs), len(self.albums), len(self.artists),
                           len(self.genres), numberofdecades)

    def getnumberofsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return len(self.songs)

    def getnumberofdecades(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        return len(self.decades.keys())

    def getnumberofgenres(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        # XXX why does len(self.genres) not work???
        # return len(self.genres)
        return len(self.genres.keys())

    def getnumberofratings(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        # XXX why does len(self.genres) not work???
        # return len(self.genres)
        return len(self.ratings.keys())

    def getnumberofalbums(self, request):
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
            return None

    def getsongs(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getsongs(request.artist, request.album, request.filters)
        except (KeyError, AttributeError, TypeError):
            return []

    def getartists(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getartists(request.filters)
        except KeyError:
            return []

    def getartist(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getartist(request.artist)
        except KeyError:
            return None

    def getalbums(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getalbums(request.artist, request.filters)
        except KeyError:
            return []

    def getalbum(self, request):
        if self.id != request.songdbid:
            raise hub.DenyRequest
        try:
            return self._getalbum(request.album)
        except KeyError:
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

        # checkpoint count used to determine the number of requests in between 
        # checkpoint calls
        self.checkpointcount = 0

        self.channel.subscribe(events.autoregistersongs, self.autoregistersongs)
        self.channel.subscribe(events.rescansongs, self.rescansongs)

    def _notify(self, event):
        """ wait until db is not busy, send event and checkpoint db regularly """
        while self.dbbusymethod():
            time.sleep(0.1)
        hub.notify(event, -100)
        self.checkpointcount += 1
        if self.checkpointcount == 100:
            hub.notify(events.checkpointdb(self.songdbid), -100)
            self.checkpointcount = 0

    def registerdirtree(self, dir):
        """ scan for songs and playlists in dir and its subdirectories, returning all items which have been scanned """
        log.debug("registerer: entering %s"% dir)
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
                        log.warning("songautoregisterer: could not enter dir %s: %s" % (path, e))
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
            songs.append(dbitem.song(songid, self.basedir,
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

        # checkpoint regularly to prevent overly large transaction logs and wait until the
        # database is not busy any more
        if songs or playlists:
            self._notify(events.checkpointdb(self.songdbid))

        registereditems.extend(playlists)
        log.debug("registerer: leaving %s"% dir)
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
            log.info(_("database %s: scanning for songs in %s") % (self.songdbid, self.basedir))

            # get all songs and playlists currently stored in the database
            oldsongs = hub.request(requests.getsongs(self.songdbid))
            oldplaylists = hub.request(requests.getplaylists(self.songdbid))

            # scan for all songs and playlists in the filesystem
            log.debug("database %s: searching for new songs" % self.songdbid)
            registereditems = self.registerdirtree(self.basedir)

            # update information for songs which have not yet been scanned (in particular
            # remove songs which are no longer present in the database)
            log.debug("database %s: removing stale songs" % self.songdbid)
            registereditemshash = {}
            for item in registereditems:
                registereditemshash[item] = None
            for song in oldsongs:
                if song not in registereditemshash:
                    self.rescansong(song)
            for playlist in oldplaylists:
                if playlist not in registereditemshash:
                    self.rescanplaylist(playlist)

            log.info(_("database %s: finished scanning for songs in %s") % (self.songdbid, self.basedir))

    def rescansongs(self, event):
        if self.songdbid == event.songdbid:
            log.info(_("database %s: rescanning %d songs") % (self.songdbid, len(event.songs)))
            for song in event.songs:
                self.rescansong(song)
            log.info(_("database %s: finished rescanning %d songs") % (self.songdbid, len(event.songs)))

