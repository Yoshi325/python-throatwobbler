# -*- coding: ISO-8859-1 -*-

# Copyright (C) 2002, 2003, 2004, 2005, 2006 Jörg Lehmann <joerg@luga.de>
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

import os.path

class song:

    def update_id3(self, othersong):
        """ merge id3 information from othersong """
        self.title = othersong.title
        self.album = othersong.album
        self.artist = othersong.artist
        self.year = othersong.year
        self.comment = othersong.comment
        self.tags = othersong.tags
        self.tracknumber = othersong.tracknumber
        self.trackcount = othersong.trackcount
        self.disknumber = othersong.disknumber
        self.diskcount = othersong.diskcount
        self.length = othersong.length
        self.size = othersong.size
        self.replaygain_track_gain = othersong.replaygain_track_gain
        self.replaygain_track_peak = othersong.replaygain_track_peak
        self.replaygain_album_gain = othersong.replaygain_album_gain
        self.replaygain_album_peak = othersong.replaygain_album_peak
        self.date_updated = time.time()




class playlist:

    # XXX just for the code

    def __init__(self, path):
        self.path = self.id = os.path.normpath(path)
        self.name = os.path.basename(path)
        if self.name.endswith(".m3u"):
            self.name = self.name[:-4]
        self.songs = []

        file = open(self.path, "r")

        for line in file.xreadlines():
            # XXX: interpret extended m3u format (especially for streams)
            # see: http://forums.winamp.com/showthread.php?s=dbec47f3a05d10a3a77959f17926d39c&threadid=65772
            if not line.startswith("#") and not chr(0) in line:
                path = line.strip()
                if not path.startswith("/"):
                    path = os.path.join(self.path, path)
                if os.path.isfile(path):
                    self.songs.append(path)
        file.close()
