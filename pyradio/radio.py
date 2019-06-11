# -*- coding: utf-8 -*-

# PyRadio: Curses based Internet Radio Player
# http://www.coderholic.com/pyradio
# Ben Dowling - 2009 - 2010
# Kirill Klenov - 2012
# Peter Stevenson (2E0PGS) - 2018
# Spiros Georgaras - 2018

import curses
import threading
import logging
import os
import random
#import signal
from sys import version as python_version, version_info
from os.path import join, basename, getmtime, getsize
from platform import system
from time import ctime, sleep
#from copy import deepcopy
#from textwrap import wrap

from .common import *
from .config_window import *
from .log import Log
from .edit import PyRadioSearch
from .themes import *
from . import player
import logging

logger = logging.getLogger(__name__)

import locale
locale.setlocale(locale.LC_ALL, "")

def rel(path):
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), path)

class PyRadio(object):
    operation_mode = NORMAL_MODE
    window_mode = NORMAL_MODE
    previous_operation_mode = NORMAL_MODE

    """ number of items (stations or playlists) in current view """
    number_of_items = 0

    playing = -1
    jumpnr = ""
    """ Help window
        also used for displaying messages,
        asking for confirmation etc. """
    helpWinContainer = None
    helpWin = None

    """ Window to display line number (jumpnr) """
    transientWin = None

    """ Used when loading new playlist.
        If the first station (selection) exists in the new playlist,
        we mark it as selected
        If the seconf station (playing) exists in the new playlist,
        we continue playing, otherwise, we stop playback """
    active_stations = [ [ '', 0 ], [ '', -1 ] ]

    # Number of stations to change with the page up/down keys
    pageChange = 5

    search = None
    _stations_search = None
    _playlists_search = None

    _last_played_station = ''

    _random_requested = False

    _theme = PyRadioTheme()
    _theme_name = 'dark'
    _theme_selector = None

    _config_win = None

    _color_config_win = None

    _player_select_win = None
    _encoding_select_win = None
    _playlist_select_win = None
    _station_select_win = None

    _old_config_encoding = ''

    #_recovery_lock = threading.Lock()

    def __init__(self, pyradio_config, play=False, req_player='', theme=''):
        self._cnf = pyradio_config
        if theme:
            self._theme_name = theme
        ind = self._cnf.current_playlist_index()
        self.selections = [ [0, 0, -1, self._cnf.stations],
                            [ind, 0, ind, self._cnf.playlists]]
        self.selection, self.startPos, self.playing, self.stations = self.selections[self.operation_mode]
        self.play = play
        self.stdscr = None
        self.requested_player = req_player
        self.number_of_items = len(self._cnf.stations)

    def __del__(self):
        self.transientWin = None

    def setup(self, stdscr):
        self.setup_return_status = True
        if not curses.has_colors():
            self.setup_return_status = False
            return
        if logger.isEnabledFor(logging.INFO):
            logger.info("GUI initialization on python v. {0} on {1}".format(python_version.replace('\n', ' ').replace('\r', ' '), system()))
            logger.info('Terminal supports {} colors'.format(curses.COLORS))
        self.stdscr = stdscr
        from pyradio import version
        self.info = " PyRadio {0} ".format(version)
        # git_description can be set at build time
        # if so, revision will be shown along with the version
        # if revision is not 0
        git_description = ''
        if git_description:
            if git_description == 'not_from_git':
                if logger.isEnabledFor(logging.INFO):
                    logger.info("RyRadio built from zip file (revision unknown)")
            else:
                git_info = git_description.split('-')
                if git_info[1] != '0':
                    self.info = " PyRadio {0}-r{1} ".format(version, git_info[1])
                if logger.isEnabledFor(logging.INFO):
                    logger.info("RyRadio built from git: https://github.com/coderholic/pyradio/commit/{0} (rev. {1})".format(git_info[-1], git_info[1]))

        try:
            curses.curs_set(0)
        except:
            pass

        curses.use_default_colors()
        self._theme._transparent = self._cnf.use_transparency
        self._theme.config_dir = self._cnf.stations_dir
        self._theme.readAndApplyTheme(self._theme_name)
        self._theme_name = self._theme.applied_theme_name

        self.log = Log()
        # For the time being, supported players are mpv, mplayer and vlc.
        try:
            self.player = player.probePlayer(requested_player=self.requested_player)(self.log, self._cnf.connection_timeout, self.connectionFailed)
        except:
            # no player
            self.operation_mode = NO_PLAYER_ERROR_MODE
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = NO_PLAYER_ERROR_MODE')

        self.stdscr.nodelay(0)
        self.setupAndDrawScreen()

        if self._stations_search is None:
            self._stations_search = PyRadioSearch(parent = self.bodyWin,
                begin_y = 0, begin_x = 0,
                boxed = True,
                has_history = True,
                box_color = curses.color_pair(5),
                caption_color = curses.color_pair(5),
                edit_color = curses.color_pair(8),
                cursor_color = curses.color_pair(6))
        if self._playlists_search is None:
            self._playlists_search = PyRadioSearch(parent = self.bodyWin,
                begin_y = 0, begin_x = 0,
                boxed = True,
                has_history = True,
                box_color = curses.color_pair(5),
                caption_color = curses.color_pair(5),
                edit_color = curses.color_pair(8),
                cursor_color = curses.color_pair(6))
        self.search = self._stations_search
        # position playlist in window
        self.bodyMaxY, self.bodyMaxX = self.bodyWin.getmaxyx()
        if self.selections[PLAYLIST_MODE][0] < self.bodyMaxY - 2:
            self.selections[PLAYLIST_MODE][1] = 0
        elif self.selections[PLAYLIST_MODE][0] > len(self._cnf.playlists) - self.bodyMaxY + 1:
            self.selections[PLAYLIST_MODE][1] = len(self._cnf.playlists) - self.bodyMaxY + 2
        else:
            self.selections[PLAYLIST_MODE][1] = self.selections[PLAYLIST_MODE][0] - int(self.bodyMaxY/2)
        self.run()

    def setupAndDrawScreen(self):
        self.maxY, self.maxX = self.stdscr.getmaxyx()

        self.headWin = None
        self.bodyWin = None
        self.footerWin = None
        self.headWin = curses.newwin(1, self.maxX, 0, 0)
        self.bodyWin = curses.newwin(self.maxY - 2, self.maxX, 1, 0)
        self.footerWin = curses.newwin(1, self.maxX, self.maxY - 1, 0)
        # txtWin used mainly for error reports
        self.txtWin = None
        self.txtWin = curses.newwin(self.maxY - 4, self.maxX - 4, 2, 2)
        self.initHead(self.info)
        # for light color scheme
         # TODO
        self.bodyWin.bkgdset(' ', curses.color_pair(5))
        self.initBody()
        self.initFooter()

        self.log.setScreen(self.footerWin)

        #self.stdscr.timeout(100)
        self.bodyWin.keypad(1)

        #self.stdscr.noutrefresh()

        curses.doupdate()

    def initHead(self, info):
        d_info = info + (self.maxX - len(info)) * ' '
        try:
            self.headWin.addstr(0, 0, d_info, curses.color_pair(4))
        except:
            pass
        rightStr = " www.coderholic.com/pyradio"
        rightStr = " https://github.com/coderholic/pyradio"
        try:
            self.headWin.addstr(0, self.maxX - len(rightStr) -1, rightStr,
                                curses.color_pair(2))
        except:
            pass
        self.headWin.bkgd(' ', curses.color_pair(7))
        self.headWin.noutrefresh()

    def initBody(self):
        """ Initializes the body/story window """
        #self.bodyWin.timeout(100)
        #self.bodyWin.keypad(1)
        self.bodyMaxY, self.bodyMaxX = self.bodyWin.getmaxyx()
        self.bodyWin.noutrefresh()
        if self.operation_mode == NO_PLAYER_ERROR_MODE:
            if self.requested_player:
                txt = """Rypadio is not able to use the player you specified.

                This means that either this particular player is not supported
                by PyRadio, or that you have simply misspelled its name.

                PyRadio currently supports three players: mpv, mplayer and vlc,
                automatically detected in this order."""
            else:
                txt = """PyRadio is not able to detect any players.

                PyRadio currently supports three players: mpv, mplayer and vlc,
                automatically detected in this order.

                Please install any one of them and try again.

                Please keep in mind that if mpv is installed, socat must be
                installed as well."""
            self.refreshNoPlayerBody(txt)
        else:
            #if self.operation_mode == MAIN_HELP_MODE:
            #    self.operation_mode = self.window_mode = NORMAL_MODE
            #    self.helpWin = None
            #    if logger.isEnabledFor(logging.DEBUG):
            #        logger.debug('MODE: MAIN_HELP_MODE => NORMAL_MODE')
            #elif self.operation_mode == PLAYLIST_HELP_MODE:
            #    self.operation_mode = self.window_mode = PLAYLIST_MODE
            #    self.helpWin = None
            #    if logger.isEnabledFor(logging.DEBUG):
            #        logger.debug('MODE: PLAYLIST_HELP_MODE =>  PLAYLIST_MODE')
            #elif self.operation_mode == THEME_HELP_MODE:
            #    self.operation_mode = self.window_mode = THEME_MODE
            #    self.helpWin = None
            #    if logger.isEnabledFor(logging.DEBUG):
            #        logger.debug('MODE: THEME_HELP_MODE =>  THEME_MODE')
            # make sure selected is visible
            self._put_selection_in_the_middle()
            self.refreshBody()

    def initFooter(self):
        """ Initializes the body/story window """
        self.footerWin.bkgd(' ', curses.color_pair(7))
        self.footerWin.noutrefresh()

    def refreshBody(self):
        if self.window_mode == CONFIG_MODE:
            self._config_win.parent = self.bodyWin
            self._config_win.init_config_win()
            self._config_win.refresh_config_win()
            if self.operation_mode == SELECT_PLAYER_MODE or \
                    self.operation_mode == SELECT_PLAYER_HELP_MODE:
                self._player_select_win.refresh_and_resize(self.bodyMaxY, self.bodyMaxX)
            elif self.operation_mode == SELECT_ENCODING_MODE or \
                    self.operation_mode == SELECT_STATION_ENCODING_MODE or \
                    self.operation_mode == SELECT_ENCODING_HELP_MODE:
                self._encoding_select_win.refresh_and_resize(self.bodyMaxY, self.bodyMaxX)
            elif self.operation_mode == SELECT_PLAYLIST_MODE or \
                    self.operation_mode == SELECT_PLAYLIST_HELP_MODE:
                self._playlist_select_win.refresh_and_resize(self.bodyWin.getmaxyx())
            elif self.operation_mode == SELECT_STATION_MODE or \
                    self.operation_mode == SELECT_STATION_HELP_MODE:
                self._station_select_win.refresh_and_resize(self.bodyWin.getmaxyx())
        else:
            self.bodyWin.erase()
            self.bodyWin.box()
            self.bodyWin.move(1, 1)
            maxDisplay = self.bodyMaxY - 1
            self._print_body_header()
            if self.number_of_items > 0:
                pad = len(str(self.startPos + self.bodyMaxY - 2))
                for lineNum in range(maxDisplay - 1):
                    i = lineNum + self.startPos
                    if i < len(self.stations):
                        self.__displayBodyLine(lineNum, pad, self.stations[i])
                    else:
                        break
            self.bodyWin.refresh()
        self._redisplay_transient_window()

    def refreshNoPlayerBody(self, a_string):
        col = curses.color_pair(5)
        self.bodyWin.bkgdset(' ', col)
        self.bodyWin.erase()
        self.bodyWin.box()
        lines = a_string.split('\n')
        lineNum = 0
        self.txtWin.bkgdset(' ', col)
        self.txtWin.erase()
        self.txtWin.erase()
        for line in lines:
            try:
                self.txtWin.addstr(lineNum , 0, line.replace('\r', '').strip(), col)
            except:
                break
            lineNum += 1
        self.bodyWin.refresh()
        self.txtWin.refresh()

    def _print_body_header(self):
        cur_mode = self.window_mode
        if cur_mode == THEME_MODE:
            cur_mode = self.previous_mode
        if cur_mode == NORMAL_MODE:
            align = 1
            w_header = self._cnf.stations_filename_only_no_extension
            if self._cnf.dirty_playlist:
                align += 1
                w_header = '*' + self._cnf.stations_filename_only_no_extension
            while len(w_header)> self.bodyMaxX - 14:
                w_header = w_header[:-1]
            self.bodyWin.addstr(0,
                    int((self.bodyMaxX - len(w_header)) / 2) - align, '[',
                    curses.color_pair(5))
            self.bodyWin.addstr(w_header,curses.color_pair(4))
            self.bodyWin.addstr(']',curses.color_pair(5))

        elif cur_mode == PLAYLIST_MODE or \
                self.operation_mode == PLAYLIST_LOAD_ERROR_MODE or \
                self.operation_mode == PLAYLIST_NOT_FOUND_ERROR_MODE:
            """ display playlists header """
            w_header = ' Select playlist to open '
            self.bodyWin.addstr(0,
                    int((self.bodyMaxX - len(w_header)) / 2),
                    w_header,
                    curses.color_pair(4))

    def __displayBodyLine(self, lineNum, pad, station):
        col = curses.color_pair(5)
        body_width = self.bodyMaxX - 2
        if lineNum + self.startPos == self.selection and \
                self.selection == self.playing:
            col = curses.color_pair(9)
            self.bodyWin.hline(lineNum + 1, 1, ' ', body_width, col)
        elif lineNum + self.startPos == self.selection:
            col = curses.color_pair(6)
            self.bodyWin.hline(lineNum + 1, 1, ' ', body_width, col)
        elif lineNum + self.startPos == self.playing:
            col = curses.color_pair(4)
            self.bodyWin.hline(lineNum + 1, 1, ' ', body_width, col)

        if self.operation_mode == PLAYLIST_MODE or \
                self.operation_mode == PLAYLIST_LOAD_ERROR_MODE or \
                    self.operation_mode == PLAYLIST_NOT_FOUND_ERROR_MODE:
            line = self._format_playlist_line(lineNum, pad, station)
        else:
            line = "{0}. {1}".format(str(lineNum + self.startPos + 1).rjust(pad), station[0])
        self.bodyWin.addstr(lineNum + 1, 1, line[:body_width], col)

    def run(self):
        if self.operation_mode == NO_PLAYER_ERROR_MODE:
            if self.requested_player:
                if ',' in self.requested_player:
                    self.log.write('None of "{}" players is available. Press any key to exit....'.format(self.requested_player))
                else:
                    self.log.write('Player "{}" not available. Press any key to exit....'.format(self.requested_player))
            else:
                self.log.write("No player available. Press any key to exit....")
            try:
                self.bodyWin.getch()
            except KeyboardInterrupt:
                pass
        else:
            #signal.signal(signal.SIGINT, self.ctrl_c_handler)
            self.log.write('Selected player: {}'.format(self._format_player_string()), help_msg=True)
            #self.log.write_right('Press ? for help')
            if self.play != 'False':
                if self.play is None:
                    num = random.randint(0, len(self.stations))
                    self._random_requested = True
                else:
                    if self.play.replace('-', '').isdigit():
                        num = int(self.play) - 1
                self.setStation(num)
                if self.number_of_items > 0:
                    self.playSelection()
                    self._goto_playing_station(changing_playlist=True)
                self.refreshBody()
                self.selections[NORMAL_MODE] = [self.selection, self.startPos, self.playing, self.stations]

            if self._cnf.foreign_file:
                """ ask to copy this playlist in config dir """
                self._print_handle_foreign_playlist()

            while True:
                try:
                    c = self.bodyWin.getch()
                    ret = self.keypress(c)
                    if (ret == -1):
                        return
                except KeyboardInterrupt:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('Ctrl-C pressed... Exiting...')
                    self.player.ctrl_c_pressed = True
                    self.ctrl_c_handler(0, 0)
                    break

    def ctrl_c_handler(self, signum, frame):
        self.ctrl_c_pressed = True
        if self._cnf.dirty_playlist:
            """ Try to auto save playlist on exit
                Do not check result!!! """
            self.saveCurrentPlaylist()
        """ Try to auto save config on exit
            Do not check result!!! """
        self._cnf.save_config()

    def _goto_playing_station(self, changing_playlist=False):
        """ make sure playing station is visible """
        if (self.player.isPlaying() or self.operation_mode == PLAYLIST_MODE) and \
            (self.selection != self.playing or changing_playlist):
            if changing_playlist:
                self.startPos = 0
            max_lines = self.bodyMaxY - 2
            if logger.isEnabledFor(logging.INFO):
                logger.info('max_lines = {0}, self.playing = {1}'.format(max_lines, self.playing))
            if self.number_of_items < max_lines:
                self.startPos = 0
            elif self.playing < self.startPos or \
                    self.playing >= self.startPos + max_lines:
                if logger.isEnabledFor(logging.INFO):
                    logger.info('=== _goto:adjusting startPos')
                if self.playing < max_lines:
                    self.startPos = 0
                    if self.playing - int(max_lines/2) > 0:
                        self.startPos = self.playing - int(max_lines/2)
                elif self.playing > self.number_of_items - max_lines:
                    self.startPos = self.number_of_items - max_lines
                else:
                    self.startPos = int(self.playing+1/max_lines) - int(max_lines/2)
            if logger.isEnabledFor(logging.INFO):
                logger.info('===== _goto:startPos = {0}, changing_playlist = {1}'.format(self.startPos, changing_playlist))
            self.selection = self.playing
            self.refreshBody()

    def _put_selection_in_the_middle(self, force=False):
        max_lines = self.bodyMaxY - 2
        if self.number_of_items < max_lines or self.selection < max_lines:
            self.startPos = 0
        elif force or self.selection < self.startPos or \
                self.selection >= self.startPos + max_lines:
            if logger.isEnabledFor(logging.INFO):
                logger.info('=== _put:adjusting startPos')
            if self.selection < max_lines:
                self.startPos = 0
                if self.selection - int(max_lines/2) > 0:
                    self.startPos = self.selection - int(max_lines/2)
            elif self.selection > self.number_of_items - max_lines:
                self.startPos = self.number_of_items - max_lines
            else:
                self.startPos = int(self.selection+1/max_lines) - int(max_lines/2)
        if logger.isEnabledFor(logging.INFO):
            logger.info('===== _put:startPos -> {0}, force = {1}'.format(self.startPos, force))

    def setStation(self, number):
        """ Select the given station number """
        # If we press up at the first station, we go to the last one
        # and if we press down on the last one we go back to the first one.
        if number < 0:
            number = len(self.stations) - 1
        elif number >= len(self.stations):
            number = 0

        self.selection = number

        maxDisplayedItems = self.bodyMaxY - 2
        if self.selection - self.startPos >= maxDisplayedItems:
            self.startPos = self.selection - maxDisplayedItems + 1
        elif self.selection < self.startPos:
            self.startPos = self.selection

    def playSelection(self):
        self.playing = self.selection
        self._last_played_station = self.stations[self.selection][0]
        stream_url = self.stations[self.selection][1].strip()
        try:
            enc = self.stations[self.selection][2].strip()
        except:
            enc = ''
        self.log.display_help_message = False
        self.log.write('Playing ' + self._last_played_station)
        try:
            self.player.play(self._last_played_station, stream_url, self.get_active_encoding(enc))
        except OSError:
            self.log.write('Error starting player.'
                           'Are you sure a supported player is installed?')

    def connectionFailed(self):
        old_playing = self.playing
        self.stopPlayer(False)
        self.selections[NORMAL_MODE][2] = -1
        if self.window_mode == NORMAL_MODE:
            if self.operation_mode == NORMAL_MODE:
                self.refreshBody()
        else:
            self.playing = old_playing
            self._redisplay_transient_window()
        if logger.isEnabledFor(logging.INFO):
            logger.info('Failed to connect to: "{}"'.format(self._last_played_station))
        self.log.write('Failed to connect to: "{}"'.format(self._last_played_station))
        if self._random_requested and \
                self.operation_mode == NORMAL_MODE:
            if logger.isEnabledFor(logging.INFO):
                logger.info('Looking for a working station (random is on)')
            self.play_random()

    def stopPlayer(self, show_message=True):
        """ stop player """
        try:
            self.player.close()
        except:
            pass
        finally:
            self.playing = -1
            if show_message:
                self.log.write('{}: Playback stopped'.format(self._format_player_string()), thread_lock=None, help_msg=True)
                #self.log.write_right('Press ? for help')
            #self.log.write('Playback stopped')

    def removeStation(self):
        if self._cnf.confirm_station_deletion:
            txt = '''Are you sure you want to delete station:
            "|{}|"?

            Press "|y|" to confirm, "|Y|" to confirm and not
            be asked again, or any other key to cancel'''

            # truncate parameter to text width
            mwidth = self._get_message_width_from_string(txt)
            msg = self.stations[self.selection][0]
            if len(msg) > mwidth - 3:
                msg = msg[:mwidth-6] + '...'

            self._show_help(txt.format(msg),
                    REMOVE_STATION_MODE, caption = ' Station Deletion ',
                    prompt = '', is_message=True)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = REMOVE_STATION_MODE')
        else:
            self.operation_mode = REMOVE_STATION_MODE
            curses.ungetch('y')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = Auto REMOVE_STATION_MODE')

    def saveCurrentPlaylist(self, stationFile =''):
        ret = self._cnf.save_playlist_file(stationFile)
        self.refreshBody()
        if ret == -1:
            self._print_save_playlist_error_1()
        elif ret == -2:
            self._print_save_playlist_error_2()
        if ret < 0 and logger.isEnabledFor(logging.DEBUG):
            logger.debug('Error saving playlist: "{}"'.format(self._cnf.stations_file))
        return ret

    def reloadCurrentPlaylist(self, mode):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug('Reloading current playlist')
        self._get_active_stations()
        txt = '''Reloading playlist. Please wait...'''
        self._show_help(txt, NORMAL_MODE, caption=' ', prompt=' ', is_message=True)
        self.jumpnr = ''
        ret = self._cnf.read_playlist_file(self._cnf.stations_file)
        if ret == -1:
            self.stations = self._cnf.playlists
            self._print_playlist_reload_error()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('Error reloading playlist: "{}"'.format(self._cnf.stations_file))
        else:
            self.number_of_items = ret
            cur_mode = self.operation_mode
            self.operation_mode = self.window_mode = NORMAL_MODE
            self._align_stations_and_refresh(NORMAL_MODE)
            if logger.isEnabledFor(logging.DEBUG):
                if cur_mode == PLAYLIST_RELOAD_CONFIRM_MODE:
                    logger.debug('MODE: PLAYLIST_RELOAD_CONFIRM_MODE -> NORMAL_MODE')
                else:
                    logger.debug('MODE: PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE -> NORMAL_MODE')
        return

    def readPlaylists(self):
        num_of_playlists, playing = self._cnf.read_playlists()
        if num_of_playlists == 0:
            txt = '''No playlists found!!!

            This should never have happened; PyRadio is missing its
            default playlist. Therefore, it has to terminate now.
            It will re-create it the next time it is lounched.
            '''
            self._show_help(txt.format(self._cnf.stations_filename_only),
                    mode_to_set = PLAYLIST_SCAN_ERROR_MODE,
                    caption = ' Error ',
                    prompt = ' Press any key to exit ',
                    is_message=True)
            if logger.isEnabledFor(logging.ERROR):
                logger.error('No playlists found!!!')
        return num_of_playlists, playing

    def _format_player_string(self):
        if self.player.PLAYER_CMD == 'cvlc':
            return 'vlc'
        return self.player.PLAYER_CMD

    def _show_theme_selector(self, changed_from_config=False):
        self.jumpnr = ''
        self._random_requested = False
        self._theme_selector = None
        self._theme_selector = PyRadioThemeSelector(self.bodyWin, self._cnf, self._theme,
                self._theme_name, self._theme._applied_theme_max_colors, self._cnf.theme,
                4, 3, 4, 5, 6, 9, self._theme.getTransparency())
                #'/home/spiros/edit.log')
        self._theme_selector.changed_from_config = changed_from_config
        self._theme_selector.show()

        if logger.isEnabledFor(logging.DEBUG):
            if self.window_mode == CONFIG_MODE:
                logger.debug('MODE: CONFIG_MODE => THEME_MODE')
            elif self.previous_mode == NORMAL_MODE:
                logger.debug('MODE: NORMAL_MODE => THEME_MODE')
            else:
                logger.debug('MODE: PLAYLIST_MODE => THEME_MODE')

    def _get_message_width_from_list(self, lines):
        mwidth = 0
        for n in lines:
            llen = len(n.replace('|', ''))
            if llen > mwidth:
                mwidth = llen
        return mwidth

    def _get_message_width_from_string(self, txt):
        lines = txt.split('\n')
        st_lines = [item.replace('\r','') for item in lines]
        lines = [item.strip() for item in st_lines]
        return self._get_message_width_from_list(lines)

    def _show_help(self, txt,
                mode_to_set=MAIN_HELP_MODE,
                caption=' Help ',
                prompt=' Press any key to hide ',
                too_small_msg='Window too small to show message',
                is_message=False):
        """ Display a help, info or question window.  """
        self.helpWinContainer = None
        self.helpWin = None
        self.operation_mode = mode_to_set
        txt_col = curses.color_pair(5)
        box_col = curses.color_pair(3)
        caption_col = curses.color_pair(4)
        lines = txt.split('\n')
        st_lines = [item.replace('\r','') for item in lines]
        lines = [item.strip() for item in st_lines]
        inner_height = len(lines) + 2
        inner_width = self._get_message_width_from_list(lines) + 4
        outer_height = inner_height + 2
        outer_width = inner_width + 2
        if (self.window_mode == CONFIG_MODE and \
                self.operation_mode > CONFIG_HELP_MODE) or \
                (self.window_mode == NORMAL_MODE and \
                self.operation_mode == SELECT_ENCODING_HELP_MODE):
            use_empty_win = True
            height_to_use = outer_height
            width_to_use = outer_width
        else:
            use_empty_win = False
            height_to_use = inner_height
            width_to_use = inner_width
        if self.maxY - 2 < outer_height or self.maxX < outer_width:
            txt = too_small_msg
            inner_height = 3
            inner_width = len(txt) + 4
            if use_empty_win:
                height_to_use = inner_height +2
                width_to_use = inner_width + 2
            else:
                height_to_use = inner_height
                width_to_use = inner_width
            if self.maxX < width_to_use:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('  ***  Window too small even to show help warning  ***')
                self.operation_mode = self.window_mode = NORMAL_MODE
                return
            lines = [ txt , ]
        if use_empty_win:
            self.helpWinContainer = curses.newwin(height_to_use,width_to_use,int((self.maxY-height_to_use)/2),int((self.maxX-width_to_use)/2))
            self.helpWinContainer.bkgdset(' ', box_col)
            self.helpWinContainer.erase()
        self.helpWin = curses.newwin(inner_height,inner_width,int((self.maxY-inner_height)/2),int((self.maxX-inner_width)/2))
        self.helpWin.bkgdset(' ', box_col)
        self.helpWin.erase()
        self.helpWin.box()
        if is_message:
            start_with = txt_col
            follow = caption_col
        else:
            start_with = caption_col
            follow = txt_col
        if caption.strip():
            self.helpWin.addstr(0, int((inner_width-len(caption))/2), caption, caption_col)
        splited = []
        for i, n in enumerate(lines):
            a_line = self._replace_starting_undesscore(n)
            if a_line.startswith('%'):
                self.helpWin.move(i + 1, 0)
                try:
                    self.helpWin.addstr('├', curses.color_pair(3))
                    self.helpWin.addstr('─' * (inner_width - 2), curses.color_pair(3))
                    self.helpWin.addstr('┤', curses.color_pair(3))
                except:
                    self.helpWin.addstr('├'.encode('utf-8'), curses.color_pair(3))
                    self.helpWin.addstr('─'.encode('utf-8') * (inner_width - 2), curses.color_pair(3))
                    self.helpWin.addstr('┤'.encode('utf-8'), curses.color_pair(3))
                self.helpWin.addstr(i + 1, inner_width-len(a_line[1:]) - 1, a_line[1:].replace('_', ' '), caption_col)
                #self.helpWin.addstr(i + 1, int((inner_width-len(a_line[1:]))/2), a_line[1:].replace('_', ' '), caption_col)
            else:
                splited = a_line.split('|')
                self.helpWin.move(i + 1, 2)
                for part, part_string in enumerate(splited):
                    if part_string.strip():
                        if part == 0 or part % 2 == 0:
                            self.helpWin.addstr(splited[part], start_with)
                        else:
                            self.helpWin.addstr(splited[part], follow)
        if prompt.strip():
            self.helpWin.addstr(inner_height - 1, int(inner_width-len(prompt)-1), prompt)
        if use_empty_win:
            self.helpWinContainer.refresh()
        self.helpWin.refresh()

    def _replace_starting_undesscore(self, a_string):
        ret = ''
        for i, ch in enumerate(a_string):
            if ch == '_':
                ret += ' '
            else:
                ret += a_string[i:]
                break
        return ret

    def _format_playlist_line(self, lineNum, pad, station):
        """ format playlist line so that if fills self.maxX """
        line = "{0}. {1}".format(str(lineNum + self.startPos + 1).rjust(pad), station[0])
        f_data = ' [{0}, {1}]'.format(station[2], station[1])
        if version_info < (3, 0):
            if len(line.decode('utf-8', 'replace')) + len(f_data.decode('utf-8', 'replace')) > self.bodyMaxX -2:
                """ this is too long, try to shorten it
                    by removing file size """
                f_data = ' [{0}]'.format(station[1])
            if len(line.decode('utf-8', 'replace')) + len(f_data.decode('utf-8', 'replace')) > self.bodyMaxX - 2:
                """ still too long. start removing chars """
                while len(line.decode('utf-8', 'replace')) + len(f_data.decode('utf-8', 'replace')) > self.bodyMaxX - 3:
                    f_data = f_data[:-1]
                f_data += ']'
            """ if too short, pad f_data to the right """
            if len(line.decode('utf-8', 'replace')) + len(f_data.decode('utf-8', 'replace')) < self.maxX - 2:
                while len(line.decode('utf-8', 'replace')) + len(f_data.decode('utf-8', 'replace')) < self.maxX - 2:
                    line += ' '
        else:
            if len(line) + len(f_data) > self.bodyMaxX -2:
                """ this is too long, try to shorten it
                    by removing file size """
                f_data = ' [{0}]'.format(station[1])
            if len(line) + len(f_data) > self.bodyMaxX - 2:
                """ still too long. start removing chars """
                while len(line) + len(f_data) > self.bodyMaxX - 3:
                    f_data = f_data[:-1]
                f_data += ']'
            """ if too short, pad f_data to the right """
            if len(line) + len(f_data) < self.maxX - 2:
                while len(line) + len(f_data) < self.maxX - 2:
                    line += ' '
        line += f_data
        return line

    def _print_help(self):
        if self.window_mode == PLAYLIST_MODE:
            txt = """Up|/|j|/|PgUp
                     Down|/|k|/|PgDown    |Change playlist selection.
                     g <n>G           |Jump to first or n-th / last playlist.
                     p                |Jump to loaded playlist.
                     Enter|/|Right|/|l    |Open selected playlist.
                     r                |Re-read playlists from disk.
                     Esc|/|q|/|Left|/|h     |Cancel.
                     %_Player Keys_
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc).
                     %_Other Keys_
                     T                |Toggle transparency."""
            self._show_help(txt, mode_to_set=PLAYLIST_HELP_MODE, caption=' Playlist Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = PLAYLIST_HELP_MODE')

        elif self.window_mode == THEME_MODE:
            self._show_theme_help()

        elif self.window_mode == CONFIG_MODE:
            if self.operation_mode == THEME_MODE or \
                    self.operation_mode == THEME_HELP_MODE:
                self._show_theme_help()
            elif self.operation_mode == SELECT_PLAYER_MODE or \
                    self.operation_mode == SELECT_PLAYER_HELP_MODE:
                self._show_config_player_help()
            elif self.operation_mode == SELECT_ENCODING_MODE or \
                    self.operation_mode == SELECT_ENCODING_HELP_MODE:
                self._show_config_encoding_help()
            elif self.operation_mode == SELECT_PLAYLIST_MODE or \
                    self.operation_mode == SELECT_PLAYLIST_HELP_MODE:
                self._show_config_playlist_help()
            elif self.operation_mode == SELECT_STATION_MODE or \
                    self.operation_mode == SELECT_STATION_HELP_MODE:
                self._show_config_station_help()
            else:
                self._show_config_help()

        elif self.operation_mode == SELECT_STATION_ENCODING_MODE or \
                self.operation_mode == SELECT_ENCODING_HELP_MODE:
            self._show_config_encoding_help()

        elif self.window_mode == NORMAL_MODE:
            txt = """Up|/|j|/|PgUp
                     Down|/|k|/|PgDown    |Change station selection.
                     g <n>G           |Jump to first or n-th / last station.
                     p                |Jump to playing station.
                     Enter|/|Right|/|l    |Play selected station.
                     r                |Select and play a random station.
                     Space|/|Left|/|h     |Stop/start playing selected station.
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc).
                     o s R            ||O|pen / |S|ave / |R|eload playlist.
                     e                |Change station's encoding.
                     DEL|,|x            |Delete selected station.
                     t T              |Load |t|heme / |T|oggle transparency.
                     c                |Open Configuration window.
                     Esc|/|q            |Quit. """
            self._show_help(txt)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = MAIN_HELP_MODE')

    def _show_playlist_recovered(self):
        txt = 'Playlist recoverd!'
        self._show_help(txt, mode_to_set=self.operation_mode, caption='',
                prompt='', is_message=True)
        # start 750 ms counter
        th = threading.Timer(0.75, self.closeRecoveryNotification)
        th.start()

    def closeRecoveryNotification(self, *arg, **karg):
        #arg[1].acquire()
        self._cnf.playlist_recovery_result = 0
        #arg[1].release()
        self.refreshBody()

    def _show_theme_help(self):
            txt = """Up|/|j|/|PgUp
                     Down|/|k|/|PgDown    |Change theme selection.
                     g <n>G           |Jump to first or n-th / last theme.
                     Enter|/|Right|/|l    |Apply selected theme.
                     Space            |Apply theme and make it default.
                     s                |Make theme default and close window.
                     Esc|/|q|/|Left|/|h     |Close window.
                     %_Player Keys_
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc)."""
            self._show_help(txt, mode_to_set=THEME_HELP_MODE, caption=' Themes Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = THEME_HELP_MODE')

    def _show_config_help(self):
            txt = """Up|/|j|/|PgUp
                     Down|/|k|/|PgDown          |Change option selection.
                     g|/|Home G|/|End           |Jump to first / last option.
                     Enter|/|Space|/|Right|/|l    |Change option value.
                     r                      |Revert to saved values.
                     d                      |Load default values.
                     s                      |Save config.
                     Esc|/|q|/|Left|/|h           |Cancel.
                     %_Player Keys_
                     -|/|+| or |,|/|.             |Change volume.
                     m v                    ||M|ute player / |S|ave volume (not in vlc)."""
            self._show_help(txt, mode_to_set=CONFIG_HELP_MODE, caption=' Configuration Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = CONFIG_HELP_MODE')

    def _show_config_player_help(self):
            txt = """Up|/|j|/|Down|/|k      |Change player selection.
                     TAB              |Move selection to other column.
                     Enter|/|Space      |Move player to other column.
                     Right|/|l          |Move player to the end of the list.
                     r                |Revert to saved values.
                     s                |Save players.
                     Esc|/|q|/|Left|/|h     |Cancel.
                     %_Player Keys_
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc)."""
            self._show_help(txt, mode_to_set=SELECT_PLAYER_HELP_MODE, caption=' Player Selection Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = SELECT_PLAYER_HELP_MODE')

    def _show_config_playlist_help(self):
            txt = """Up|/|j|/|PgUp
                     Down|/|k|/|PgDown    |Change playlist selection.
                     g <n>G           |Jump to first or n-th / last playlist.
                     Enter|/|Space
                     Right|/|l          |Select default playlist.
                     r                |Revert to saved value.
                     Esc|/|q|/|Left|/|h     |Canel.
                     %_Player Keys_
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc)."""
            self._show_help(txt, mode_to_set=SELECT_PLAYLIST_HELP_MODE, caption=' Playlist Selection Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = SELECT_PLAYLIST_HELP_MODE')

    def _show_config_station_help(self):
            txt = """Up|/|j|/|PgUp
                     Down|/|k|/|PgDown    |Change station selection.
                     g <n>G           |Jump to first or n-th / last station.
                     Enter|/|Space
                     Right|/|l          |Select default station.
                     r                |Revert to saved value.
                     Esc|/|q|/|Left|/|h     |Canel.
                     %_Player Keys_
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc)."""
            self._show_help(txt, mode_to_set=SELECT_STATION_HELP_MODE, caption=' Station Selection Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = SELECT_STATION_HELP_MODE')

    def _show_config_encoding_help(self):
            txt = """Arrows|/|h|/|j|/|k|/|l|/|PgUp|/|/PgDn
                     g|/|Home|/|G|/|End     |Change encoding selection.
                     Enter|/|Space|/|s    |Save encoding.
                     r                |Revert to saved value.
                     Esc|/|q            |Cancel.
                     %_Player Keys_
                     -|/|+| or |,|/|.       |Change volume.
                     m v              ||M|ute player / |S|ave volume (not in vlc)."""
            self._show_help(txt, mode_to_set=SELECT_ENCODING_HELP_MODE, caption=' Encoding Selection Help ')
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE = SELECT_ENCODING_HELP_MODE')

    def _print_not_implemented_yet(self):
        self.previous_mode = self.operation_mode
        txt = '''This feature has not been implemented yet...
        '''
        self._show_help(txt, NOT_IMPLEMENTED_YET_MODE,
                caption = ' PyRadio ',
                prompt = ' Press any key... ',
                is_message=True)

    def _print_handle_foreign_playlist(self):
        txt ='''This is a "|foreign|" playlist (i.e. it does not
            reside in PyRadio's config directory).
            If you want to be able to easily load it again
            in the future, it should be copied there.

            Do you want to copy it in the config directory?

            Press "|y|" to confirm or "|n|" to reject'''
        self._show_help(txt, FOREIGN_PLAYLIST_ASK_MODE,
                caption = ' Foreign playlist ',
                prompt = ' ',
                is_message=True)

    def _print_foreign_playlist_message(self):
        """ reset previous message """
        self.operation_mode = self.window_mode = NORMAL_MODE
        self.refreshBody()
        """ display new message """
        txt='''A playlist by this name:
            __"|{0}|"
            already exists in the config directory.

            This playlist was saved as:
            __"|{1}|"
            '''.format(self._cnf.foreign_filename_only_no_extension,
                    self._cnf.stations_filename_only_no_extension)
        self._show_help(txt, FOREIGN_PLAYLIST_MESSAGE_MODE,
                caption = ' Foreign playlist ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_foreign_playlist_copy_error(self):
        """ reset previous message """
        self.operation_mode = self.window_mode = NORMAL_MODE
        self.refreshBody()
        txt ='''Foreign playlist copying |failed|!

            Make sure the file is not open with another
            application and try to load it again
            '''
        self._show_help(txt, FOREIGN_PLAYLIST_COPY_ERROR_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_playlist_recovery_error(self):
        if self._cnf.playlist_recovery_result == 1:
            txt = """Both a playlist file (CSV) and a playlist backup
file (TXT) exist for the selected playlist. In
this case, PyRadio would try to delete the CSV
file, and then rename the TXT file to CSV.\n
Unfortunately, deleting the CSV file has failed,
so you have to manually address the issue.
"""
        else:
            txt = """A playlist backup file (TXT) has been found for
the selected playlist. In this case, PyRadio would
try to rename this file to CSV.\n
Unfortunately, renaming this file has failed, so
you have to manually address the issue.
"""
        self._show_help(txt, PLAYLIST_RECOVERY_ERROR_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_playlist_not_found_error(self):
        txt = """Playlist |not| found!

            This means that the playlist file was deleted
            (or renamed) some time after you opened the
            Playlist Selection window.
            """
        self._show_help(txt, PLAYLIST_NOT_FOUND_ERROR_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_playlist_load_error(self):
        txt = """Playlist loading |failed|!

            This means that either the file is corrupt,
            or you are not permitted to access it.
            """
        self._show_help(txt, PLAYLIST_LOAD_ERROR_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_playlist_reload_error(self):
        txt ='''Playlist reloading |failed|!

            You have probably edited the playlist with an
            external program. Please re-edit it and make
            sure that only one "," exists in each line.
            '''
        self._show_help(txt, PLAYLIST_RELOAD_ERROR_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_playlist_reload_confirmation(self):
        txt ='''This playlist has not been modified within
            PyRadio. Do you still want to reload it?

            Press "|y|" to confirm, "|Y|" to confirm and not
            be asked again, or any other key to cancel'''
        self._show_help(txt, PLAYLIST_RELOAD_CONFIRM_MODE,
                caption = ' Playlist Reload ',
                prompt = ' ',
                is_message=True)

    def _print_playlist_dirty_reload_confirmation(self):
        txt ='''This playlist has been modified within PyRadio.
            If you reload it now, all modifications will be
            lost. Do you still want to reload it?

            Press "|y|" to confirm, "|Y|" to confirm and not be
            asked again, or "|n|" to cancel'''
        self._show_help(txt, PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE,
                caption = ' Playlist Reload ',
                prompt = ' ',
                is_message=True)

    def _print_save_modified_playlist(self, mode):
        txt ='''This playlist has been modified within
            PyRadio. Do you want to save it?

            If you choose not to save it now, all
            modifications will be lost.

            Press "|y|" to confirm, "|Y|" to confirm and not
            be asked again, "|n|" to reject, or "|q|" or
            "|ESCAPE|" to cancel'''
        self._show_help(txt, mode,
                caption = ' Playlist Modified ',
                prompt = ' ',
                is_message=True)
        if logger.isEnabledFor(logging.DEBUG):
            if mode == ASK_TO_SAVE_PLAYLIST_WHEN_OPENING_PLAYLIST_MODE:
                logger.debug('MODE = ASK_TO_SAVE_PLAYLIST_WHEN_OPENING_PLAYLIST_MODE')
            else:
                logger.debug('MODE = ASK_TO_SAVE_PLAYLIST_WHEN_EXITING_MODE')

    def _print_save_playlist_error_1(self):
        txt = '''Saving current playlist |failed|!

            Could not open file for writing
            "|{}|"
            '''
        self._show_help(txt.format(self._cnf.stations_file.replace('.csv', '.txt')),
                mode_to_set = SAVE_PLAYLIST_ERROR_1_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_save_playlist_error_2(self):
        txt = '''Saving current playlist |failed|!

            You will find a copy of the saved playlist in
            "|{}|"

            Pyradio will open this file when the playlist
            is opened in the future.
            '''
        self._show_help(txt.format(self._cnf.stations_file.replace('.csv', '.txt')),
                mode_to_set = SAVE_PLAYLIST_ERROR_2_MODE,
                caption = ' Error ',
                prompt = ' Press any key ',
                is_message=True)

    def _print_config_save_error(self):
        txt = '''An error occured while saving the configuration file!

            |PyRadio| will try to |restore| your previous settings,
            but in order to do so, it has to |terminate now!

            '''
        self._show_help(txt,
                mode_to_set = CONFIG_SAVE_ERROR_MODE,
                caption = ' Error Saving Config ',
                prompt = ' Press any key to exit ',
                is_message=True)

    def _align_stations_and_refresh(self, cur_mode):
        need_to_scan_playlist = False
        """ refresh reference """
        self.stations = self._cnf.stations
        self.number_of_items = len(self.stations)

        if self.number_of_items == 0:
            """ The playlist is empty """
            if self.player.isPlaying():
                self.stopPlayer()
            self.playing,self.selection, self.stations, \
                self.number_of_items = (-1, 0, 0, 0)
            self.operation_mode = self.window_mode = NORMAL_MODE
            self.refreshBody()
            return
        else:
            #if logger.isEnabledFor(logging.DEBUG):
            #    logger.debug('self.playing = {}'.format(self.playing))
            if cur_mode == REMOVE_STATION_MODE:
                """ Remove selected station """
                if self.player.isPlaying():
                    if self.selection == self.playing:
                        self.stopPlayer()
                        self.playing = -1
                    elif self.selection < self.playing:
                        self.playing -= 1
                else:
                    self.playing = -1

                if self.selection > self.number_of_items - self.bodyMaxY:
                    self.startPos -= 1
                    if self.selection >= self.number_of_items:
                        self.selection -= 1
                if self.startPos < 0:
                    self.startPos = 0
            else:
                if self.player.isPlaying():
                    """ The playlist is not empty """
                    if self.playing > self.number_of_items - 1:
                        """ Previous playing station is now invalid
                            Need to scan playlist """
                        need_to_scan_playlist = True
                    else:
                        if self.stations[self.playing][0] == self.active_stations[1][0]:
                            """ ok, self.playing found, just find selection """
                            self.selection = self._get_station_id(self.active_stations[0][0])
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug('** Selected station is {0} at {1}'.format(self.stations[self.selection], self.selection))
                        else:
                            """ station playing id changed, try previous station """
                            self.playing -= 1
                            if self.playing == -1:
                                self.playing = len(self.stations) - 1
                            if self.stations[self.playing][0] == self.active_stations[1][0]:
                                """ ok, self.playing found, just find selection """
                                self.selection = self._get_station_id(self.active_stations[0][0])
                                if logger.isEnabledFor(logging.DEBUG):
                                    logger.debug('** Selection station is {0} at {1}'.format(self.stations[self.playing], self.playing))
                            else:
                                """ self.playing still not found, have to scan playlist """
                                need_to_scan_playlist = True
                else:
                    """ not playing, can i get a selection? """
                    need_to_scan_playlist = True

            if need_to_scan_playlist:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('Scanning playlist for stations...')
                self.selection, self.playing = self._get_stations_ids((
                    self.active_stations[0][0],
                    self.active_stations[1][0]))
                if self.playing == -1:
                    self.stopPlayer()
                need_to_calulate_position = True

                """ calculate new position """
                if self.player.isPlaying():
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('Setting playing station at {}'.format(self.playing))
                    self.setStation(self.playing)
                else:
                    if self.selection == -1:
                        self.selection = 0
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('Setting selection station at {}'.format(self.selection))
                    self.setStation(self.selection)

        if self.selection < 0:
            """ make sure we have a valid selection """
            self.selection = 0
            self.startPos = 0
        """ make sure playing station is visible """
        self._goto_playing_station(changing_playlist=True)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug('self.selection = {0}, self.playing = {1}, self.startPos = {2}'.format(self.selection, self.playing, self.startPos))

        self.selections[self.operation_mode] = [self.selection, self.startPos, self.playing, self._cnf.stations]
        self.refreshBody()

    def _open_playlist(self):
        """ open playlist """
        self._get_active_stations()
        self.jumpnr = ''
        self._random_requested = False
        txt = '''Reading playlists. Please wait...'''
        self._show_help(txt, NORMAL_MODE, caption=' ', prompt=' ', is_message=True)
        self.selections[self.operation_mode] = [self.selection, self.startPos, self.playing, self._cnf.stations]
        self.operation_mode = self.window_mode = PLAYLIST_MODE
        self.search = self._playlists_search
        self.selection, self.startPos, self.playing, self.stations = self.selections[self.operation_mode]
        self.number_of_items, self.playing = self.readPlaylists()
        self.stations = self._cnf.playlists
        if self.number_of_items == 0:
            return
        else:
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: NORMAL_MODE -> PLAYLIST_MODE')
            return

    def _get_station_id(self, find):
        for i, a_station in enumerate(self.stations):
            if a_station[0] == find:
                return i
        return -1

    def _get_stations_ids(self, find):
        ch = -2
        i_find = [ -1, -1 ]
        debug_str = ('selection', 'playing')
        for j, a_find in enumerate(find):
            if a_find.strip():
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('** Looking for {0} station: "{1}"'.format(debug_str[j], a_find))

                for i, a_station in enumerate(self.stations):
                    if i_find[j] == -1:
                        if j == 1 and find[0] == find[1]:
                            """ No need to scan again for the same station """
                            i_find[1] = i_find[0]
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug('** Got it at {}'.format(i_find[0]))
                            break
                        if a_station[0] == a_find:
                            i_find[j] = i
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug('** Found at {}'.format(i))
                            ch += 1
                            if ch == 0:
                                break
        return i_find

    def _get_active_stations(self):
        if self.player.isPlaying():
            self.active_stations = [
                    [ self.stations[self.selection][0], self.selection ],
                    [ self.stations[self.playing][0], self.playing ]
                    ]
        else:
            if self.number_of_items > 0:
                self.active_stations = [
                        [ self.stations[self.selection][0], self.selection ],
                        [ '', -1 ] ]
            else:
                self.active_stations = [
                        [ '', self.selection ],
                        [ '', -1 ] ]

    def get_active_encoding(self, an_encoding):
        if an_encoding:
            return an_encoding
        else:
            return self._cnf.default_encoding

    def _redisplay_transient_window(self):
        if self._cnf.playlist_recovery_result == -1:
            self._show_playlist_recovered()
            return
        if self.operation_mode == MAIN_HELP_MODE or \
            self.operation_mode == PLAYLIST_HELP_MODE or \
            self.operation_mode == THEME_HELP_MODE or \
            self.operation_mode == CONFIG_HELP_MODE or \
            self.operation_mode == SELECT_PLAYER_HELP_MODE or \
            self.operation_mode == SELECT_PLAYLIST_HELP_MODE or \
            self.operation_mode == SELECT_STATION_HELP_MODE:
                self._print_help()
        elif self.operation_mode == SELECT_ENCODING_HELP_MODE:
            if self.window_mode == NORMAL_MODE:
                self._encoding_select_win.refresh_and_resize(self.bodyMaxY, self.bodyMaxX)
            self._print_help()
        if self.operation_mode == SELECT_STATION_ENCODING_MODE:
            self._encoding_select_win.refresh_and_resize(self.bodyMaxY, self.bodyMaxX)
        elif self.operation_mode == PLAYLIST_NOT_FOUND_ERROR_MODE:
            self._print_playlist_not_found_error()
        elif self.operation_mode == PLAYLIST_LOAD_ERROR_MODE:
            self._print_playlist_load_error()
        elif self.operation_mode == ASK_TO_SAVE_PLAYLIST_WHEN_OPENING_PLAYLIST_MODE or \
                self.operation_mode == ASK_TO_SAVE_PLAYLIST_WHEN_EXITING_MODE:
            self._print_save_modified_playlist(self.operation_mode)
        elif self.operation_mode == PLAYLIST_RELOAD_CONFIRM_MODE:
            self._print_playlist_reload_confirmation()
        elif self.operation_mode == PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE:
            self._print_playlist_dirty_reload_confirmation()
        elif self.operation_mode == PLAYLIST_RELOAD_ERROR_MODE:
            self._print_playlist_reload_error()
        elif self.operation_mode == SAVE_PLAYLIST_ERROR_1_MODE:
            self._print_save_playlist_error_1()
        elif self.operation_mode == SAVE_PLAYLIST_ERROR_2_MODE:
            self._print_save_playlist_error_2()
        elif self.operation_mode == REMOVE_STATION_MODE:
            self.removeStation()
        elif self.operation_mode == FOREIGN_PLAYLIST_ASK_MODE:
            self._print_handle_foreign_playlist()
        elif self.operation_mode == FOREIGN_PLAYLIST_MESSAGE_MODE:
            self._print_foreign_playlist_message()
        elif self.operation_mode == FOREIGN_PLAYLIST_COPY_ERROR_MODE:
            self._print_foreign_playlist_copy_error()
        elif self.operation_mode == SEARCH_NORMAL_MODE or \
                self.operation_mode == SEARCH_PLAYLIST_MODE:
            self.search.show(self.bodyWin, repaint=True)
        elif self.operation_mode == THEME_MODE:
            self._theme_selector.parent = self.bodyWin
            self._show_theme_selector()
        elif self.operation_mode == PLAYLIST_RECOVERY_ERROR_MODE:
            self._print_playlist_recovery_error()

    def play_random(self):
        # Pick a random radio station
        if self.number_of_items > 0:
            self.setStation(random.randint(0, len(self.stations)))
            self.playSelection()
            self._put_selection_in_the_middle(force=True)
            self.refreshBody()

    def _toggle_transparency(self, changed_from_config_window=False, force_value=None):
        """ Toggles theme trasparency.

            changed_from_config_window is used to inhibit toggling from within
            Config Window when 'T' is pressed.

            force_value will set trasparency if True or False,
            or toggle trasparency if None
        """
        if self.window_mode == CONFIG_MODE and not changed_from_config_window:
            return
        self._theme.toggleTransparency(force_value)
        self._cnf.use_transparency = self._theme.getTransparency()
        if self.operation_mode == THEME_MODE:
            self._theme_selector.transparent = self._cnf.use_transparency
        self.headWin.refresh()
        self.bodyWin.refresh()
        self.footerWin.refresh()
        if self._config_win:
            self._config_win._config_options['use_transparency'][1] = self._cnf.use_transparency
            if not changed_from_config_window:
                self._config_win._saved_config_options['use_transparency'][1] = self._cnf.use_transparency
                self._config_win._old_use_transparency = self._cnf.use_transparency

    def _show_theme_selector_from_config(self):
                self.previous_mode = self.operation_mode
                self.operation_mode = THEME_MODE
                self._show_theme_selector(changed_from_config=True)

    def _show_config_window(self):
        if self._config_win is None:
            self._config_win = PyRadioConfigWindow(self.bodyWin,
                self._cnf,
                self._toggle_transparency,
                self._show_theme_selector_from_config)
        else:
            self._config_win.parent = self.bodyWin
            self._config_win.refresh_config_win()

    def keypress(self, char):

        if self.operation_mode == CONFIG_SAVE_ERROR_MODE:
            # exit
            return -1

        if self.operation_mode == NOT_IMPLEMENTED_YET_MODE:
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = self.previous_mode
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: Exiting NOT_IMPLEMENTED_YET_MODE')
            self.refreshBody()
            return

        if char in (ord('t'), ):
            # only open it on main modes
            if self.window_mode != THEME_MODE and  \
                    self.operation_mode <= SEARCH_PLAYLIST_MODE:
                self.jumpnr = ''
                self._random_requested = False
                self._config_win = None
                if self.operation_mode == NORMAL_MODE:
                    self.selections[self.operation_mode] = [self.selection, self.startPos, self.playing, self.stations]
                self.previous_mode = self.operation_mode
                self.operation_mode = self.window_mode = THEME_MODE
                self._show_theme_selector()
                return

        if self.operation_mode <= PLAYLIST_MODE and char == ord('p'):
            self._goto_playing_station()
            return

        if self.operation_mode == CONFIG_MODE:
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                if char in (ord('r'), ord('d')):
                    self._player_select_win = None
                    self._encoding_select_win = None
                    self._playlist_select_win = None
                    self._station_select_win = None
                ret, ret_list = self._config_win.keypress(char)
                if ret == SELECT_PLAYER_MODE:
                    """ Config > Select Player """
                    self.operation_mode = SELECT_PLAYER_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: CONFIG_MODE => SELECT_PLAYER_MODE')
                    if self._player_select_win is None:
                        self._player_select_win = PyRadioSelectPlayer(self.bodyMaxY,
                                self.bodyMaxX, self._config_win._config_options['player'][1])
                    else:
                        self._player_select_win._parent_maxY, self._player_select_win._parent_maxX = self.bodyWin.getmaxyx()
                    self._player_select_win.init_window()
                    self._player_select_win.refresh_win()
                    self._player_select_win.setPlayers(self._config_win._config_options['player'][1])
                    self._player_select_win.refresh_selection()

                elif ret == SELECT_ENCODING_MODE:
                    """ Config > Select Default Encoding """
                    self.operation_mode = SELECT_ENCODING_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: CONFIG_MODE => SELECT_ENCODING_MODE')
                    if self._encoding_select_win is None:
                        self._encoding_select_win = PyRadioSelectEncodings(self.bodyMaxY,
                                self.bodyMaxX, self._cnf.default_encoding)
                    else:
                        self._encoding_select_win._parent_maxY, self._encoding_select_win._parent_maxX = self.bodyWin.getmaxyx()
                    self._encoding_select_win.init_window()
                    self._encoding_select_win.refresh_win()
                    self._encoding_select_win.setEncoding(self._config_win._config_options['default_encoding'][1])

                elif ret == SELECT_PLAYLIST_MODE:
                    """ Config > Select Default Playlist """
                    self.operation_mode = SELECT_PLAYLIST_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: CONFIG_MODE => SELECT_PLAYLIST_MODE')
                    if self._playlist_select_win is None:
                        self._playlist_select_win = PyRadioSelectPlaylist(self.bodyWin,
                                self._cnf.stations_dir,
                                self._config_win._config_options['default_playlist'][1])
                    else:
                        self._playlist_select_win._parent_maxY, self._playlist_select_win._parent_maxX = self.bodyWin.getmaxyx()
                    self._playlist_select_win.init_window()
                    self._playlist_select_win.refresh_win()
                    self._playlist_select_win.setPlaylist(self._config_win._config_options['default_playlist'][1])

                elif ret == SELECT_STATION_MODE:
                    """ Config > Select Default Station """
                    self.operation_mode = SELECT_STATION_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: CONFIG_MODE => SELECT_STATION_MODE')
                    if self._station_select_win is None:
                        self._station_select_win = PyRadioSelectStation(self.bodyWin,
                                self._cnf.stations_dir,
                                self._config_win._config_options['default_playlist'][1],
                                self._config_win._config_options['default_station'][1])
                    else:
                        self._station_select_win._parent_maxY, self._station_select_win._parent_maxX = self.bodyWin.getmaxyx()
                        self._station_select_win.update_playlist_and_station(self._config_win._config_options['default_playlist'][1], self._config_win._config_options['default_station'][1])
                    self._station_select_win.init_window()
                    self._station_select_win.refresh_win()
                    self._station_select_win.setStation(self._config_win._config_options['default_station'][1])

                elif ret >= 0:
                    msg = ( 'Error saving config. Press any key to exit...',
                            'Config saved successfully!',
                            'Config saved - Restarting playback (encoding changed)')
                    self.operation_mode = self.window_mode = NORMAL_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: CONFIG_MODE => NORMAL_MODE')
                    self.bodyWin.box()
                    self._print_body_header()
                    self.refreshBody()
                    if ret == 0:
                        ret = self._cnf.save_config()
                        if ret == -1:
                            # Error saving config
                            if self.player.isPlaying():
                                self.stopPlayer()
                                self.refreshBody()
                            self.log.display_help_message = False
                            self.log.write(msg[0], thread_lock=None, help_msg=False)
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug('MODE: NORMAL_MODE => CONFIG_SAVE_ERROR_MODE')
                            self._print_config_save_error()
                        elif ret == 0:
                            # Config saved successfully
                            if self.player.isPlaying():
                                if self._cnf.opts['default_encoding'][1] == self._old_config_encoding:
                                    self.log.write(msg[1])
                                    self.player.threadUpdateTitle(self.player.status_update_lock)
                                else:
                                    self.log.write(msg[2])
                                    self.player.threadUpdateTitle(self.player.status_update_lock)
                                    sleep(1.5)
                                    self.playSelection()
                            else:

                                self.log.write(msg[1], thread_lock=None, help_msg=True)
                            self._old_config_encoding = self._cnf.opts['default_encoding'][1]
                            if self._config_win:
                                self._config_win._old_use_transparency = self._cnf.use_transparency
                    else:
                        # restore transparency, if necessary
                        if self._config_win._config_options['use_transparency'][1] != self._config_win._saved_config_options['use_transparency'][1]:
                            self._toggle_transparency(changed_from_config_window=False,
                                    force_value=self._config_win._saved_config_options['use_transparency'][1])
                        # restore theme, if necessary
                        if self._cnf.opts['theme'][1] != self._config_win._config_options['theme'][1]:
                            #self._config_win._apply_a_theme(self._cnf.opts['theme'][1])
                            self._theme.readAndApplyTheme(self._cnf.opts['theme'][1])
                            self._theme_name = self._cnf.theme
                            curses.doupdate()
                        # make sure config is not saved
                        self._config_win._saved_config_options['dirty_config'][1] = False
                        self._cnf.dirty_config = False
                    # clean up
                    self._player_select_win = None
                    self._encoding_select_win = None
                    self._playlist_select_win = None
                    self._station_select_win = None
                    self._config_win = None
                return

        elif self.operation_mode == SELECT_PLAYER_MODE:
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                ret, ret_list = self._player_select_win.keypress(char)
                if ret >= 0:
                    if ret == 0:
                        new_players = ','.join(ret_list)
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('new_players = {}'.format(new_players))
                        self._config_win._config_options['player'][1] = new_players
                    self.operation_mode = self.window_mode = CONFIG_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SELECT_PLAYER_MODE => CONFIG_MODE')
                    self._config_win.refresh_config_win()
                return

        elif self.operation_mode == SELECT_STATION_ENCODING_MODE:
            """ select station's encoding from main window """
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                ret, ret_encoding = self._encoding_select_win.keypress(char)
                if ret >= 0:
                    if ret == 0:
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('new station encoding = {}'.format(ret_encoding))
                        """ save encoding and playlist """
                        if self._old_station_encoding == 'utf-8':
                            self._old_station_encoding = ''
                        if ret_encoding == 'utf-8':
                            ret_encoding = ''
                        if self._old_station_encoding != ret_encoding:
                            self._cnf.dirty_playlist = True
                            logger.info('self.stations[self.selection] = {}'.format(self.stations[self.selection]))
                            self.stations[self.selection][2] = ret_encoding
                            if self.player.isPlaying():
                                curses.ungetch('l')
                        #self._config_win._config_options['default_encoding'][1] = ret_encoding
                    self.operation_mode = self.window_mode = NORMAL_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SELECT_STATION_ENCODING_MODE => NORMAL_MODE')
                    self.refreshBody()
                    self._encoding_select_win = None
                return

        elif self.operation_mode == SELECT_ENCODING_MODE:
            """ select global encoding from config window """
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                ret, ret_encoding = self._encoding_select_win.keypress(char)
                if ret >= 0:
                    if ret == 0:
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('new encoding = {}'.format(ret_encoding))
                        self._config_win._config_options['default_encoding'][1] = ret_encoding
                    self.operation_mode = self.window_mode = CONFIG_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SELECT_ENCODING_MODE => CONFIG_MODE')
                    self._config_win.refresh_config_win()
                return

        elif self.operation_mode == SELECT_PLAYLIST_MODE:
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                ret, ret_playlist = self._playlist_select_win.keypress(char)
                if ret >= 0:
                    if ret == 0:
                        self._config_win._config_options['default_playlist'][1] = ret_playlist
                        if ret_playlist == self._config_win._saved_config_options['default_playlist'][1]:
                            self._config_win._config_options['default_station'][1] = self._config_win._saved_config_options['default_station'][1]
                        else:
                            self._config_win._config_options['default_station'][1] = 'False'
                        if logger.isEnabledFor(logging.INFO):
                            logger.info('New default_playlist = "{0}", New default station = "{1}"'.format(ret_playlist, self._config_win._config_options['default_station'][1]))
                    self.operation_mode = self.window_mode = CONFIG_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SELECT_PLAYLIST_MODE => CONFIG_MODE')
                    self._config_win.refresh_config_win()
                return

        elif self.operation_mode == SELECT_STATION_MODE:
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                ret, ret_station = self._station_select_win.keypress(char)
                if ret >= 0:
                    if ret == 0:
                        if logger.isEnabledFor(logging.INFO):
                            logger.info('New default station = "{}"'.format(ret_station))
                        self._config_win._config_options['default_station'][1] = ret_station
                    self.operation_mode = self.window_mode = CONFIG_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SELECT_STATION_MODE => CONFIG_MODE')
                    self._config_win.refresh_config_win()
                return

        elif self.operation_mode == THEME_MODE:
            if char not in (ord('m'), ord('v'), ord('.'),
                    ord(','), ord('+'), ord('-'), ord('T'),
                    ord('?'), ord('#'), curses.KEY_RESIZE):
                theme_id, save_theme = self._theme_selector.keypress(char)

                if theme_id == -1:
                    """ cancel or hide """
                    self._theme_name = self._theme_selector._applied_theme_name
                    if self._config_win:
                        self._config_win._config_options['theme'][1] = self._theme_selector._applied_theme_name
                    self._theme_selector = None
                    self.operation_mode = self.window_mode = self.previous_mode
                    if self.operation_mode == NORMAL_MODE:
                        self.selection, self.startPos, self.playing, self.stations = self.selections[self.operation_mode]
                    if logger.isEnabledFor(logging.DEBUG):
                        if self.window_mode == CONFIG_MODE:
                            logger.debug('MODE: THEME_MODE => CONFIG_MODE')
                        elif self.operation_mode == NORMAL_MODE:
                            logger.debug('MODE: THEME_MODE => NORMAL_MODE')
                        else:
                            logger.debug('MODE: THEME_MODE => PLAYLIST_MODE')
                    self.refreshBody()

                elif theme_id >= 0:
                    """ valid theme selection """
                    self._theme_name = self._theme_selector.theme_name(theme_id)
                    if self._config_win:
                        self._config_win._config_options['theme'][1] = self._theme_name
                        self._config_win._saved_config_options['theme'][1] = self._theme_name
                    if logger.isEnabledFor(logging.INFO):
                        logger.info('Activating theme: {}'.format(self._theme_name))
                    self._theme.readAndApplyTheme(self._theme_name,
                            theme_path=self._theme_selector._themes[theme_id][1])
                    curses.doupdate()
                    # update config window
                    if self._config_win:
                        self._config_win._config_options['theme'][1] = self._theme_name
                    if self.window_mode == CONFIG_MODE:
                        save_theme = True
                    # make default
                    if save_theme:
                        self._cnf.theme = self._theme_name
                        if logger.isEnabledFor(logging.INFO):
                            logger.info('Setting default theme: {}'.format(self._theme_name))
                return

        if char in (ord('#'), curses.KEY_RESIZE):
            if self.player.isPlaying():
                self.log.display_help_message = False
            self.setupAndDrawScreen()
            max_lines = self.bodyMaxY - 2
            if self.selection >= self.number_of_items - max_lines and \
                    self.number_of_items > max_lines:
                self.startPos = self.number_of_items - max_lines
                self.refreshBody()
            return

        elif self.operation_mode == NO_PLAYER_ERROR_MODE:
            """ if no player, don't serve keyboard """
            return

        elif self.operation_mode == SEARCH_NORMAL_MODE or \
                self.operation_mode == SEARCH_PLAYLIST_MODE:
            self._random_requested = False
            ret = self.search.keypress(self.search._edit_win, char)
            if ret == 0:
                # perform search
                if self.operation_mode == SEARCH_NORMAL_MODE:
                    self.operation_mode = self.window_mode = NORMAL_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SEARCH_NORMAL_MODE -> NORMAL_MODE')
                else:
                    self.operation_mode = self.window_mode = PLAYLIST_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: SEARCH_PLAYLIST_MODE -> PLAYLIST_MODE')
                self.refreshBody()
                sel = self.selection + 1
                if sel == len(self.stations):
                    sel = 0
                ret = self.search.get_next(self.stations, sel)
                if ret is None:
                    self.search.string = ''
                else:
                    self.setStation(ret)
                    self.refreshBody()
            elif ret == -1:
                # cancel search
                if self.operation_mode == SEARCH_NORMAL_MODE:
                    self.operation_mode = self.window_mode = NORMAL_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: Cancel SEARCH_NORMAL_MODE -> NORMAL_MODE')
                else:
                    self.operation_mode = self.window_mode = PLAYLIST_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: Cancel SEARCH_PLAYLIST_MODE -> PLAYLIST_MODE')
                self.refreshBody()
                return

        #elif char in (ord('/'), ):
        #    self.jumpnr = ''
        #    self._random_requested = False
        #    if self.operation_mode == NORMAL_MODE or \
        #            self.operation_mode == PLAYLIST_MODE:
        #        #self.search.string = '부'
        #        self.search.show(self.bodyWin)
        #        if self.operation_mode == NORMAL_MODE:
        #            self.operation_mode = SEARCH_NORMAL_MODE
        #            if logger.isEnabledFor(logging.DEBUG):
        #                logger.debug('MODE: NORMAL_MODE -> SEARCH_NORMAL_MODE')
        #        else:
        #            self.operation_mode = SEARCH_PLAYLIST_MODE
        #            if logger.isEnabledFor(logging.DEBUG):
        #                logger.debug('MODE: PLAYLIST_MODE -> SEARCH_PLAYLIST_MODE')
        #    return

        #elif char in (ord('n'), ) and \
        #        (self.operation_mode == NORMAL_MODE or \
        #        self.operation_mode == PLAYLIST_MODE):
        #    self.jumpnr = ''
        #    self._random_requested = False
        #    """ search forward """
        #    if self.search.string:
        #        sel = self.selection + 1
        #        if sel == len(self.stations):
        #            sel = 0
        #        ret = self.search.get_next(self.stations, sel)
        #        if ret is not None:
        #            self.setStation(ret)
        #            self.refreshBody()
        #    else:
        #            curses.ungetch('/')
        #    return

        #elif char in (ord('N'), ) and \
        #        (self.operation_mode == NORMAL_MODE or \
        #        self.operation_mode == PLAYLIST_MODE):
        #    self.jumpnr = ''
        #    self._random_requested = False
        #    """ search backwards """
        #    if self.search.string:
        #        sel = self.selection - 1
        #        if sel < 0:
        #            sel = len(self.stations) - 1
        #        ret = self.search.get_previous(self.stations, sel)
        #        if ret is not None:
        #            self.setStation(ret)
        #            self.refreshBody()
        #    else:
        #        curses.ungetch('/')
        #    return

        elif char in (ord('T'), ):
            self._toggle_transparency()
            return

        elif char in (ord('+'), ord('='), ord('.')):
            self.jumpnr = ''
            if self.player.isPlaying():
                if self.player.playback_is_on:
                    self.player.volumeUp()
                else:
                    if logger.isEnabledFor(logging.INFO):
                        logger.info('Volume adjustment inhibited because playback is off')
            return

        elif char in (ord('-'), ord(',')):
            self.jumpnr = ''
            if self.player.isPlaying():
                if self.player.playback_is_on:
                    self.player.volumeDown()
                else:
                    if logger.isEnabledFor(logging.INFO):
                        logger.info('Volume adjustment inhibited because playback is off')
            return

        elif char in (ord('m'), ):
            self.jumpnr = ''
            if self.player.isPlaying():
                if self.player.playback_is_on:
                    self.player.toggleMute()
                else:
                    if logger.isEnabledFor(logging.INFO):
                        logger.info('Muting inhibited because playback is off')
            return

        elif char in (ord('v'), ):
            self.jumpnr = ''
            if self.player.isPlaying():
                if self.player.playback_is_on:
                    ret_string = self.player.save_volume()
                    if ret_string:
                        self.log.write(ret_string)
                        self.player.threadUpdateTitle(self.player.status_update_lock)
                else:
                    if logger.isEnabledFor(logging.INFO):
                        logger.info('Volume save inhibited because playback is off')
            return

        elif self.operation_mode == PLAYLIST_SCAN_ERROR_MODE:
            """ exit due to scan error """
            self.stopPlayer()
            return -1

        elif self.operation_mode == PLAYLIST_RECOVERY_ERROR_MODE:
            self._cnf.playlist_recovery_result = 0
            self.operation_mode = PLAYLIST_MODE
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: PLAYLIST_RECOVERY_ERROR_MODE -> PLAYLIST_MODE')
            self.refreshBody()
            return

        elif self.operation_mode == MAIN_HELP_MODE:
            """ Main help in on, just update """
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = self.window_mode = NORMAL_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: MAIN_HELP_MODE -> NORMAL_MODE')
            return

        elif self.operation_mode == THEME_HELP_MODE:
            """ Theme help in on, hide it """
            self.helpWinContainer = None
            self.helpWin = None
            if self.window_mode == CONFIG_MODE:
                self.operation_mode = THEME_MODE
            else:
                self.operation_mode = self.window_mode = THEME_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: THEME_HELP_MODE -> THEME_MODE')
            return

        elif self.operation_mode == CONFIG_HELP_MODE:
            """ Main help in on, just update """
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = self.window_mode = CONFIG_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: CONFIG_HELP_MODE -> CONFIG_MODE')
            return

        elif self.operation_mode == SELECT_PLAYER_HELP_MODE:
            """ Main help in on, just update """
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = SELECT_PLAYER_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: SELECT_PLAYER_HELP_MODE -> SELECT_PLAYER_MODE')
            return

        elif self.operation_mode == SELECT_ENCODING_HELP_MODE:
            """ Main help in on, just update """
            self.helpWinContainer = None
            self.helpWin = None
            if self.window_mode == NORMAL_MODE:
                self.operation_mode = SELECT_STATION_ENCODING_MODE
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: SELECT_ENCODING_HELP_MODE -> SELECT_STATION_ENCODING_MODE')
            else:
                self.operation_mode = SELECT_ENCODING_MODE
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: SELECT_ENCODING_HELP_MODE -> SELECT_ENCODING_MODE')
            #self.setupAndDrawScreen()
            self.refreshBody()
            return

        elif self.operation_mode == SELECT_PLAYLIST_HELP_MODE:
            """ Main help in on, just update """
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = SELECT_PLAYLIST_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: SELECT_PLAYLIST_HELP_MODE -> SELECT_PLAYLIST_MODE')
            return

        elif self.operation_mode == SELECT_STATION_HELP_MODE:
            """ Main help in on, just update """
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = SELECT_STATION_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: SELECT_STATION_HELP_MODE -> SELECT_STATION_MODE')
            return

        elif self.operation_mode == ASK_TO_SAVE_PLAYLIST_WHEN_EXITING_MODE:
            if char in (ord('y'), ord('Y')):
                if char == ord('Y'):
                    self._cnf.auto_save_playlist = True
                ret = self.saveCurrentPlaylist()
                if ret == -1:
                    # do not exit
                    return
                # exit program
                self.ctrl_c_handler(0,0)
                return -1
            elif char in (ord('n'), ):
                # exit program
                self._cnf.save_config()
                return -1
            elif char in (curses.KEY_EXIT, ord('q'), 27):
                self.bodyWin.nodelay(True)
                char = self.bodyWin.getch()
                self.bodyWin.nodelay(False)
                if char == -1:
                    """ ESCAPE """
                    self._cnf.save_config()
                    return -1
            return

        elif self.operation_mode == ASK_TO_SAVE_PLAYLIST_WHEN_OPENING_PLAYLIST_MODE:
            if char in (ord('y'), ord('Y')):
                if char == ord('Y'):
                    self._cnf.auto_save_playlist = True
                ret = self.saveCurrentPlaylist()
                if ret == 0:
                    self._open_playlist()
            elif char in (ord('n'), ):
                    self._open_playlist()
            elif char in (curses.KEY_EXIT, ord('q'), 27):
                self.bodyWin.nodelay(True)
                char = self.bodyWin.getch()
                self.bodyWin.nodelay(False)
                if char == -1:
                    """ ESCAPE """
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: Cancel ASK_TO_SAVE_PLAYLIST_WHEN_OPENING_PLAYLIST_MODE -> NORMAL_MODE')
                    self.operation_mode = self.window_mode = NORMAL_MODE
                    self.refreshBody()
            return

        elif self.operation_mode == PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE:
            if char in (ord('y'), ord('Y')):
                if char == ord('Y'):
                    self._cnf.confirm_playlist_reload = False
                self.reloadCurrentPlaylist(PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE)
            elif char in (ord('n'), ):
                """ close confirmation message """
                self.stations = self._cnf.stations
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: Cancel PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE -> NORMAL_MODE')
                self.operation_mode = self.window_mode = NORMAL_MODE
                self.operation_mode = NORMAL_MODE
                logger.info('*** mode = {}'.format(self.operation_mode))
                self.refreshBody()
            else:
                pass
            return

        elif self.operation_mode == PLAYLIST_RELOAD_CONFIRM_MODE:
            if char in (ord('y'), ord('Y')):
                if char == ord('Y'):
                    self._cnf.confirm_playlist_reload = False
                self.reloadCurrentPlaylist(PLAYLIST_DIRTY_RELOAD_CONFIRM_MODE)
            else:
                """ close confirmation message """
                self.stations = self._cnf.stations
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: Cancel PLAYLIST_RELOAD_CONFIRM_MODE -> NORMAL_MODE')
                self.operation_mode = self.window_mode = NORMAL_MODE
                self.operation_mode = NORMAL_MODE
                self.refreshBody()
            return

        elif self.operation_mode == PLAYLIST_RELOAD_ERROR_MODE:
            """ close error message """
            self.stations = self._cnf.stations
            self.operation_mode = self.window_mode = NORMAL_MODE
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug('MODE: PLAYLIST_RELOAD_ERROR_MODE -> NORMAL_MODE')
            return

        elif self.operation_mode == PLAYLIST_HELP_MODE or \
                self.operation_mode == PLAYLIST_LOAD_ERROR_MODE or \
                    self.operation_mode == PLAYLIST_NOT_FOUND_ERROR_MODE:
            """ close playlist help """
            self.operation_mode = self.window_mode = PLAYLIST_MODE
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                if self.operation_mode == PLAYLIST_HELP_MODE:
                    logger.debug('MODE: PLAYLIST_HELP_MODE -> PLAYLIST_MODE')
                elif self.operation_mode == PLAYLIST_LOAD_ERROR_MODE:
                    logger.debug('MODE: PLAYLIST_LOAD_ERROR_MODE -> PLAYLIST_MODE')
                else:
                    logger.debug('MODE: PLAYLIST_NOT_FOUND_ERROR_MODE -> PLAYLIST_MODE')
            return

        elif self.operation_mode == SAVE_PLAYLIST_ERROR_1_MODE or \
                self.operation_mode == SAVE_PLAYLIST_ERROR_2_MODE:
            """ close error message """
            if logger.isEnabledFor(logging.DEBUG):
                if self.operation_mode == SAVE_PLAYLIST_ERROR_1_MODE:
                    logger.debug('MODE: SAVE_PLAYLIST_ERROR_1_MODE -> NORMAL_MODE')
                else:
                    logger.debug('MODE: SAVE_PLAYLIST_ERROR_2_MODE -> NORMAL_MODE')
            self.operation_mode = self.window_mode = NORMAL_MODE
            self.refreshBody()
            return

        elif self.operation_mode == REMOVE_STATION_MODE:
            if char in (ord('y'), ord('Y')):
                self._get_active_stations()
                deleted_station, self.number_of_items = self._cnf.remove_station(self.selection)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('Deleted station: "{}"'.format(deleted_station[0]))
                self.operation_mode = self.window_mode = NORMAL_MODE
                self._align_stations_and_refresh(REMOVE_STATION_MODE)
                if char == ord('Y'):
                    self._cnf.confirm_station_deletion = False
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: REMOVE_STATION_MODE -> NORMAL_MODE')
            else:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: Cancel REMOVE_STATION_MODE -> NORMAL_MODE')
            self.operation_mode = self.window_mode = NORMAL_MODE
            #self.setupAndDrawScreen()
            self.refreshBody()
            return

        elif self.operation_mode == FOREIGN_PLAYLIST_ASK_MODE:
            if char in (ord('y'), ):
                ret = self._cnf.copy_playlist_to_config_dir()
                if ret == 0:
                    self.operation_mode = self.window_mode = NORMAL_MODE
                    self.refreshBody()
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: FOREIGN_PLAYLIST_ASK_MODE -> NORMAL_MODE')
                elif ret == 1:
                    self._print_foreign_playlist_message()
                else:
                    """ error """
                    self._print_foreign_playlist_copy_error()
            elif char in (ord('n'), ):
                self.operation_mode = self.window_mode = NORMAL_MODE
                self.refreshBody()
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('MODE: Cancel FOREIGN_PLAYLIST_ASK_MODE -> NORMAL_MODE')
            return

        elif self.operation_mode == FOREIGN_PLAYLIST_MESSAGE_MODE or \
                self.operation_mode == FOREIGN_PLAYLIST_COPY_ERROR_MODE:
            """ Just update """
            self.helpWinContainer = None
            self.helpWin = None
            self.operation_mode = self.window_mode = NORMAL_MODE
            self.refreshBody()
            if logger.isEnabledFor(logging.DEBUG):
                if self.operation_mode == FOREIGN_PLAYLIST_MESSAGE_MODE:
                    logger.debug('MODE: FOREIGN_PLAYLIST_MESSAGE_MODE -> NORMAL_MODE')
                else:
                    logger.debug('MODE: FOREIGN_PLAYLIST_COPY_ERROR_MODE -> NORMAL_MODE')
            return

        else:

            if char in (ord('?'), ):
                self.jumpnr = ''
                self._random_requested = False
                self._print_help()
                return

            if char in (curses.KEY_END, ):
                self.jumpnr = ''
                self._random_requested = False
                if self.number_of_items > 0:
                    self.setStation(-1)
                    self.jumpnr = ""
                    self.refreshBody()
                return

            if char in (ord('G'), ):
                self._random_requested = False
                if self.number_of_items > 0:
                    if self.jumpnr == "":
                        self.setStation(-1)
                    else:
                        force_center = False
                        jumpto=min(int(self.jumpnr)-1,len(self.stations)-1)
                        jumpto=max(0,jumpto)
                        if jumpto < self.startPos - 1 or \
                                jumpto > self.startPos + self.bodyMaxY:
                            force_center = True
                        self.setStation(jumpto)
                        self._put_selection_in_the_middle(force=force_center)
                    self.jumpnr = ""
                    self.refreshBody()
                return

            if char in map(ord,map(str,range(0,10))):
                self._random_requested = False
                if self.number_of_items > 0:
                    self.jumpnr += chr(char)
                    return
            else:
                self._random_requested = False
                self.jumpnr = ""

            if char in (ord('g'), curses.KEY_HOME):
                self.jumpnr = ''
                self._random_requested = False
                self.setStation(0)
                self.refreshBody()
                return

            if char in (curses.KEY_EXIT, ord('q'), 27) or \
                    (self.operation_mode == PLAYLIST_MODE and \
                    char in (ord('h'), curses.KEY_LEFT)):
                self.bodyWin.nodelay(True)
                char = self.bodyWin.getch()
                self.bodyWin.nodelay(False)
                if char == -1:
                    """ ESCAPE """
                    self.jumpnr = ''
                    self._random_requested = False
                    if self.operation_mode == PLAYLIST_MODE:
                        """ return to stations view """
                        self.jumpnr = ''
                        self.selections[self.operation_mode] = [self.selection, self.startPos, self.playing, self._cnf.playlists]
                        self.operation_mode = self.window_mode = NORMAL_MODE
                        self.search = self._stations_search
                        self.selection, self.startPos, self.playing, self.stations = self.selections[self.operation_mode]
                        self.stations = self._cnf.stations
                        self.refreshBody()
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('MODE: Cancel PLAYLIST_MODE -> NORMAL_MODE')
                        return
                    else:
                        """ exit """
                        if self.player:
                            self.stopPlayer()
                        if self._cnf.dirty_playlist:
                            if self._cnf.auto_save_playlist:
                                # save playlist and exit
                                ret = self.saveCurrentPlaylist()
                                #if ret == -1:
                                #    # do not exit program
                                #    return
                            else:
                                # ask to save playlist
                                self._print_save_modified_playlist(ASK_TO_SAVE_PLAYLIST_WHEN_EXITING_MODE)
                                return
                        #else:
                        #    self._open_playlist()
                        self.ctrl_c_handler(0,0)
                        return -1
                else:
                    return

            if char in (curses.KEY_DOWN, ord('j')):
                self.jumpnr = ''
                self._random_requested = False
                if self.number_of_items > 0:
                    self.setStation(self.selection + 1)
                    self.refreshBody()
                return

            if char in (curses.KEY_UP, ord('k')):
                self.jumpnr = ''
                self._random_requested = False
                if self.number_of_items > 0:
                    self.setStation(self.selection - 1)
                    self.refreshBody()
                return

            if char in (curses.KEY_PPAGE, ):
                self.jumpnr = ''
                self._random_requested = False
                if self.number_of_items > 0:
                    sel = self.selection - self.pageChange
                    if sel < 0 and self.selection > 0:
                        sel = 0
                    self.setStation(sel)
                    self.refreshBody()
                return

            if char in (curses.KEY_NPAGE, ):
                self.jumpnr = ''
                self._random_requested = False
                if self.number_of_items > 0:
                    sel = self.selection + self.pageChange
                    if self.selection == len(self.stations) - 1:
                        sel = 0
                    elif sel >= len(self.stations):
                        sel = len(self.stations) - 1
                    self.setStation(sel)
                    self.refreshBody()
                return

            if self.operation_mode == NORMAL_MODE:
                if char == ord('c'):
                    self._old_config_encoding = self._cnf.opts['default_encoding'][1]
                    # open config window
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: NORMAL_MODE => CONFIG_MODE')
                    self.operation_mode = self.window_mode = CONFIG_MODE
                    if not self.player.isPlaying():
                        self.log.write('Selected player: {}'.format(self._format_player_string()), help_msg=True)
                    self._show_config_window()
                    return

                elif char in (ord('e'), ):
                    self._old_station_encoding = self.stations[self.selection][2]
                    if self._old_station_encoding == '':
                        self._old_station_encoding = 'utf-8'
                    logger.info('encoding = {}'.format(self._old_station_encoding))
                    self.operation_mode = SELECT_STATION_ENCODING_MODE
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('MODE: NORMAL_MODE => SELECT_STATION_ENCODING_MODE')
                    self._encoding_select_win = PyRadioSelectEncodings(self.bodyMaxY,
                            self.bodyMaxX, self._old_station_encoding)
                    self._encoding_select_win.init_window()
                    self._encoding_select_win.refresh_win()
                    self._encoding_select_win.setEncoding(self._old_station_encoding)

                elif char in (ord('o'), ):
                    self.jumpnr = ''
                    self._random_requested = False
                    if self._cnf.dirty_playlist:
                        if self._cnf.auto_save_playlist:
                            # save playlist and open playlist
                            pass
                            ret = self.saveCurrentPlaylist()
                            if ret == 0:
                                self._open_playlist()
                        else:
                            # ask to save playlist
                            self._print_save_modified_playlist(ASK_TO_SAVE_PLAYLIST_WHEN_OPENING_PLAYLIST_MODE)
                    else:
                        self._open_playlist()
                    return

                elif char in (curses.KEY_ENTER, ord('\n'), ord('\r'),
                        curses.KEY_RIGHT, ord('l')):
                    self.jumpnr = ''
                    self._random_requested = False
                    if self.number_of_items > 0:
                        self.playSelection()
                        self.refreshBody()
                        #self.setupAndDrawScreen()
                    return

                elif char in (ord(' '), curses.KEY_LEFT, ord('h')):
                    self.jumpnr = ''
                    self._random_requested = False
                    if self.number_of_items > 0:
                        if self.player.isPlaying():
                            self.stopPlayer()
                        else:
                            self.playSelection()
                        self.refreshBody()
                    return

                elif char in(ord('x'), curses.KEY_DC):
                    self.jumpnr = ''
                    self._random_requested = False
                    if self.number_of_items > 0:
                        self.removeStation()
                    return

                elif char in(ord('s'), ):
                    self.jumpnr = ''
                    self._random_requested = False
                    if self.number_of_items > 0 and \
                            self._cnf.dirty_playlist:
                        self.saveCurrentPlaylist()
                    return

                elif char in (ord('r'), ):
                    self.jumpnr = ''
                    self._random_requested = True
                    # Pick a random radio station
                    self.play_random()
                    return

                elif char in (ord('R'), ):
                    self.jumpnr = ''
                    self._random_requested = False
                    # Reload current playlist
                    if self._cnf.dirty_playlist:
                        if self._cnf.confirm_playlist_reload:
                            self._print_playlist_dirty_reload_confirmation()
                        else:
                            self.operation_mode = PLAYLIST_RELOAD_CONFIRM_MODE
                            curses.ungetch('y')
                    else:
                        if self._cnf.confirm_playlist_reload:
                            self._print_playlist_reload_confirmation()
                        else:
                            self.operation_mode = PLAYLIST_RELOAD_CONFIRM_MODE
                            curses.ungetch('y')
                    return

            elif self.operation_mode == PLAYLIST_MODE:
                self._random_requested = False

                if char in (curses.KEY_ENTER, ord('\n'), ord('\r'),
                        curses.KEY_RIGHT, ord('l')):
                    self.jumpnr = ''
                    """ return to stations view """
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug('Loading playlist: "{}"'.format(self.stations[self.selection][-1]))
                    ret = self._cnf.read_playlist_file(self.stations[self.selection][-1])
                    if ret == -1:
                        self.stations = self._cnf.playlists
                        self._print_playlist_load_error()
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('Error loading playlist: "{}"'.format(self.stations[self.selection][-1]))
                        return
                    elif ret == -2:
                        self.stations = self._cnf.playlists
                        self._print_playlist_not_found_error()
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('Playlist not found: "{}"'.format(self.stations[self.selection][-1]))
                        return
                    elif ret == -7:
                        self._print_playlist_recovery_error()
                        return
                    else:
                        self.number_of_items = ret
                        self.selections[self.operation_mode] = [self.selection, self.startPos, self.playing, self._cnf.playlists]
                        self.operation_mode = self.window_mode = NORMAL_MODE
                        self.selection, self.startPos, self.playing, self.stations = self.selections[self.operation_mode]
                        self._align_stations_and_refresh(PLAYLIST_MODE)
                        self.search = self._stations_search
                        if self.playing < 0:
                            self._put_selection_in_the_middle(force=True)
                            self.refreshBody()
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug('MODE: PLAYLIST_MODE -> NORMAL_MODE')
                    return

                elif char in (ord('r'), ):
                    self.jumpnr = ''
                    """ read playlists from disk """
                    txt = '''Reading playlists. Please wait...'''
                    self._show_help(txt, PLAYLIST_MODE, caption=' ', prompt=' ', is_message=True)
                    old_playlist = self._cnf.playlists[self.selection][0]
                    self.number_of_items, self.playing = self.readPlaylists()
                    if self.number_of_items > 0:
                        """ refresh reference """
                        self.stations = self._cnf.playlists
                        if self.playing == -1:
                            self.selections[self.operation_mode] = [0, 0, -1, self._cnf.playlists]
                        else:
                            self.selections[self.operation_mode] = (self.selection, self.startPos, self.playing, self._cnf.playlists)
                        self.refreshBody()

# pymode:lint_ignore=W901
