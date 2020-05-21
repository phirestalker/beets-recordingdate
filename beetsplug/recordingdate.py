# -- coding: utf-8 --
from __future__ import division, absolute_import, print_function

from beets.plugins import BeetsPlugin
from beets import autotag, library, ui, util, config
from beets.autotag import hooks
import mediafile

import musicbrainzngs
musicbrainzngs.set_useragent(
    "Beets recording date plugin",
    "0.2",
    "http://github.com/tweitzel"
)


class RecordingDatePlugin(BeetsPlugin):
    importing = False

    def __init__(self):
        super(RecordingDatePlugin, self).__init__()
        self.import_stages = [self.on_import]
        self.config.add({
            'auto': True,
            'force': False,
            'write_over': False,
            'relations': {'edit', 'first track release', 'remaster'},
        })
        #grab global MusicBrainz host setting
        musicbrainzngs.set_hostname(config['musicbrainz']['host'].get())
        musicbrainzngs.set_rate_limit(1, config['musicbrainz']['ratelimit'].get())
        for recording_field in (
             u'recording_year',
             u'recording_month',
             u'recording_day',
             u'recording_disambiguation'):
            field = mediafile.MediaField(
                mediafile.MP3DescStorageStyle(recording_field),
                mediafile.MP4StorageStyle('----:com.apple.iTunes:{}'.format(
                    recording_field)),
                mediafile.StorageStyle(recording_field))
            self.add_media_field(recording_field, field)

    def commands(self):
        recording_date_command = ui.Subcommand(
            'recordingdate',
            help="Retrieve the date of the first known recording of a track.",
            aliases=['rdate'])
        recording_date_command.func = self.func
        return [recording_date_command]

    def func(self, lib, opts, args):
        query = ui.decargs(args)
        self.recording_date(lib, query)

    def recording_date(self, lib, query):
        for item in lib.items(query):
            self.process_file(item)

    def on_import(self, session, task):
        if self.config['auto']:
            self.importing = True
            for item in task.imported_items():
                self.process_file(item)

    def process_file(self, item):
        item_formatted = format(item)

        if not item.mb_trackid:
            self._log.info(u'Skipping track with no mb_trackid: {0}',
                           item_formatted)
            return
        # check for the recording_year and if it exists and not empty
        # skips the track if force is not configured
        if u'recording_year' in item and item.recording_year and not self.config['force']:
            self._log.info(u'Skipping already processed track: {0}', item_formatted)
            return
        # Get the MusicBrainz recording info.
        (recording_date, disambig) = self.get_first_recording_year(
            item.mb_trackid)
        if not recording_date:
            self._log.info(u'Recording ID not found: {0} for track {0}',
                           item.mb_trackid,
                           item_formatted)
            return
        # Apply.
        write = False
        for recording_field in ('year', 'month', 'day'):
            if recording_field in recording_date.keys():
                item[u'recording_' +
                     recording_field] = recording_date[recording_field]
                # writes over the year tag if configured
                if self.config['write_over'] and recording_field == u'year':
                    item[recording_field] = recording_date[recording_field]
                    self._log.info(u'overwriting year field for: {0}', item_formatted)
                write = True
        if disambig is not None:
            item[u'recording_disambiguation'] = str(disambig)
            write = True
        if write:
            self._log.info(u'Applying changes to {0}', item_formatted)
            item.store()
            if not self.importing:
                item.write()

        else:
            self._log.info(u'Error: {0}', recording_date)

    def _make_date_values(self, date_str):
        date_parts = date_str.split('-')
        date_values = {}
        for key in ('year', 'month', 'day'):
            if date_parts:
                date_part = date_parts.pop(0)
                try:
                    date_num = int(date_part)
                except ValueError:
                    continue
                date_values[key] = date_num
        return date_values

    def _recurse_relations(self, mb_track_id, oldest_release, relation_type):
        x = musicbrainzngs.get_recording_by_id(
            mb_track_id,
            includes=['releases', 'recording-rels'])

        if 'recording-relation-list' in x['recording'].keys():
            # recurse down into edits and remasters.
            # Note remasters are deprecated in musicbrainz, but some entries
            # may still exist.
            for subrecording in x['recording']['recording-relation-list']:
                if ('direction' in subrecording.keys() and
                        subrecording['direction'] == 'backward'):
                    continue
                # skip new relationship category samples
                if subrecording['type'] not in self.config['relations'].as_str_seq():
                    continue
                if 'artist' in x['recording'].keys() and x['recording']['artist'] != subrecording['artist']:
                    self._log.info(
                        u'Skipping relation with arist {0} that does not match {1}',
                        subrecording['artist'], x['recording']['artist'])
                    continue
                (oldest_release, relation_type) = self._recurse_relations(
                    subrecording['target'],
                    oldest_release,
                    subrecording['type'])
        for release in x['recording']['release-list']:
            if 'date' not in release.keys():
                # A release without a date. Skip over it.
                continue
            release_date = self._make_date_values(release['date'])
            if (oldest_release['year'] is None or
                    oldest_release['year'] > release_date['year']):
                oldest_release = release_date
            elif oldest_release['year'] == release_date['year']:
                if ('month' in release_date.keys() and
                        'month' in oldest_release.keys() and
                        oldest_release['month'] > release_date['month']):
                    oldest_release = release_date
        return (oldest_release, relation_type)

    def get_first_recording_year(self, mb_track_id):
        relation_type = None
        oldest_release = {'year': None}
        (oldest_release, relation_type) = self._recurse_relations(
            mb_track_id,
            oldest_release,
            relation_type)
        return (oldest_release, relation_type)
