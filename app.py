#!/bin/env python
# -*- coding: utf-8 -*-
"""
    OpenID Example
    ~~~~~~~~~~~~~~

    This simple application shows how to integrate OpenID in your application.

    This example requires SQLAlchemy as a dependency.

    :copyright: (c) 2010 by Armin Ronacher.
    :license: BSD, see LICENSE for more details.
"""

import datetime
import json
import os
import time
import urllib.request
from operator import itemgetter

import bbcode
from flask import Flask, render_template, request, g, session, flash, \
    redirect
from flask_openid import OpenID
from openid.extensions import pape
from sqlalchemy import create_engine, ForeignKey, Column, Integer, String, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from flask_session import Session

# setup flask
app = Flask(__name__)
if os.getenv('ENV') == 'dev':
    from dotenv import load_dotenv

    load_dotenv()

    app.config.update(
        DATABASE_URI=os.getenv('DATABASE_URI_DEV'),
        TIME_GAP=30,  # 1 day in seconds
        TEMPLATES_AUTO_RELOAD=True,
    )
else:
    # Fix for SQLALchemy 1.4.x not connecting to Heroku Postgres
    uri = os.getenv('DATABASE_URL')

    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)

    app.config.update(
        DATABASE_URI=os.getenv('DATABASE_URL'),
        TIME_GAP=86400,  # 1 day in seconds
    )

app.config.update(
    SECRET_KEY=os.getenv('STEAM_API_KEY'),
    MAX_LEN_MOTD=280,  # Length of a tweet
    MAX_LEN_NOTE=5000,
    DEFAULT_MIN_GAME_TIME=300,
)

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# setup flask-openid
oid = OpenID(app, safe_roots=[], extension_responses=[pape.Response])

# setup bbcode
parser = bbcode.Parser(replace_links=False)
parser.add_simple_formatter('h1', '<h1>%(value)s</h1>')
parser.add_simple_formatter('h2', '<h2>%(value)s</h2>')
parser.add_simple_formatter('h3', '<h3>%(value)s</h3>')
parser.add_simple_formatter('url', '<a href>%(value)s</a>')
parser.add_simple_formatter('img', '<img src=%(value)s></img>')
parser.add_simple_formatter('previewyoutube',
                            '<iframe '
                            'width="560" '
                            'height="315" '
                            'src="https://www.youtube.com/embed/%(value)s" '
                            'title="YouTube video player" '
                            'frameborder="0" '
                            'allow="accelerometer; '
                            'autoplay; '
                            'clipboard-write; '
                            'encrypted-media; '
                            'gyroscope; '
                            'picture-in-picture" '
                            'allowfullscreen>'
                            '</iframe>')


def render_youtube(tag_name, value, options, parent, context):
    previewyoutube = ''
    if 'previewyoutube' in options:
        previewyoutube = options['previewyoutube']

    return '<iframe src="https://www.youtube.com/embed/%s"></iframe>' % previewyoutube


parser.add_formatter('previewyoutube', render_youtube)

# setup sqlalchemy
engine = create_engine(app.config['DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=True,
                                         bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


def init_db():
    Base.metadata.create_all(bind=engine)


class Players(Base):
    """
    __tablename__ = 'players'
    steamid = Column(Integer, primary_key=True)
    personaname = Column(String(60))
    profileurl = Column(String(200))
    """
    __tablename__ = 'players'
    steamid = Column(BigInteger, primary_key=True)
    personaname = Column(String(60))
    profileurl = Column(String(200))

    def __init__(self, steamid, personaname, profileurl):
        self.steamid = steamid
        self.personaname = personaname
        self.profileurl = profileurl


class Games(Base):
    """
    __tablename__ = 'games'
    appid = Column(Integer, primary_key=True)
    top_player_steamid = Column(Integer, ForeignKey("players.steamid"))
    """
    __tablename__ = 'games'
    appid = Column(BigInteger, primary_key=True)
    top_player_steamid = Column(BigInteger, ForeignKey("players.steamid"))

    def __init__(self, appid, top_player_steamid):
        self.appid = appid
        self.top_player_steamid = top_player_steamid


class GamesNotes(Base):
    """
    __tablename__ = 'games_notes'
    msg_id = Column(Integer, primary_key=True)
    appid = Column(Integer, ForeignKey("games.appid"))
    player_steamid = Column(Integer, ForeignKey("players.steamid"))
    msg = Column(String(5000))
    """
    __tablename__ = 'games_notes'
    msg_id = Column(Integer, primary_key=True)
    appid = Column(BigInteger, ForeignKey("games.appid"))
    player_steamid = Column(BigInteger, ForeignKey("players.steamid"))
    msg = Column(String(app.config['MAX_LEN_NOTE']))

    def __init__(self, appid, player_steamid, msg):
        self.appid = appid
        self.player_steamid = player_steamid
        self.msg = msg


class TopMessages(Base):
    """
    __tablename__ = 'top_messages'
    msg_id = Column(Integer, primary_key=True)
    appid = Column(Integer, ForeignKey("games.appid"))
    player_steamid = Column(Integer, ForeignKey("players.steamid"))
    msg = Column(String(280))  # Length of a tweet
    date = Column(DateTime, server_default=func.utcnow())
    """
    __tablename__ = 'top_messages'
    msg_id = Column(Integer, primary_key=True)
    appid = Column(BigInteger, ForeignKey("games.appid"))
    player_steamid = Column(BigInteger, ForeignKey("players.steamid"))
    msg = Column(String(app.config['MAX_LEN_MOTD']))
    timestamp = Column(BigInteger)

    def __init__(self, appid, player_steamid, msg, timestamp):
        self.appid = appid
        self.player_steamid = player_steamid
        self.msg = msg
        self.timestamp = timestamp


@app.template_filter('ctime')
def timectime(s):
    return datetime.datetime.fromtimestamp(s)


@app.template_filter('type')
def typetype(s):
    return type(s)


@app.context_processor
def utility_processor():
    def time_now():
        return int(time.time())

    return dict(time_now=time_now)


@app.route('/', methods=['GET', 'POST'])
def index():
    # if we are already logged in, redirect to games_index
    if 'openid' in session:
        return redirect('/games_index')

    return redirect('/login')


@app.before_request
def before_request():
    g.player = None

    if 'openid' in session:
        steamid = int(session['openid'].split('/')[-1])
        g.player = Players.query.filter_by(steamid=steamid).first()


@app.route('/login', methods=['GET', 'POST'])
@oid.loginhandler
def login():
    if g.player is not None:
        return redirect('games_index')

    if request.method == 'POST':
        openid = request.form.get('openid')

        if openid:
            pape_req = pape.Request([])
            return oid.try_login(openid, extensions=[pape_req])

    return render_template('login.html')


@oid.after_login
def validate_login(resp):
    session['openid'] = resp.identity_url
    steamid = session['openid'].split('/')[-1]

    if 'pape' in resp.extensions:
        pape_resp = resp.extensions['pape']
        session['auth_time'] = pape_resp.auth_time

    g.player = Players.query.filter_by(steamid=steamid).first()
    if g.player is not None:
        return redirect('/games_index')

    get_summaries = "http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"

    with urllib.request.urlopen(f"{get_summaries}/?key={app.config['SECRET_KEY']}&steamids={steamid}") as url:
        data = json.load(url)['response']['players'][0]
        personaname = data['personaname']
        profileurl = data['profileurl']

        db_session.add(Players(int(steamid), personaname, profileurl))
        db_session.commit()

    return redirect('/games_index')


@app.route('/logout')
def logout():
    session.pop('openid', None)
    flash(u'You have been signed out')
    return redirect('/')


@app.route('/games_index')
def games_index():
    if 'openid' in session:
        steamid = session['openid'].split('/')[-1]
        min_game_time = session.get('min_game_time', app.config['DEFAULT_MIN_GAME_TIME'])

        get_owned_games = "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
        args = "include_appinfo=true&format=json"
        try:
            if 'player_owned_games' not in session:
                with urllib.request.urlopen(
                        f"{get_owned_games}/?key={app.config['SECRET_KEY']}&steamid={steamid}&{args}") as url:
                    session.update(player_owned_games=json.load(url)['response']['games'])

            games_ls = []

            for game_data in session.get('player_owned_games', []):
                if int(game_data['playtime_forever']) >= min_game_time:
                    games_ls.append(game_data)

            games_ls_sorted = sorted(games_ls, key=itemgetter(
                'playtime_forever'), reverse=True)

            return render_template('games_index.html', games_ls=games_ls_sorted)

        except KeyError:
            flash("Could not load games from your profile, try playing some games and then try again later.")
            return redirect('/logout')

    return redirect('/login')


@app.route('/game', methods=['GET'])
def game():
    if 'openid' in session:
        steamid = int(session['openid'].split('/')[-1])

        if 'appid' in request.args:
            appid = int(request.args['appid'])

            # Get top msg (MOTD)
            top_msg_query = TopMessages.query.filter_by(appid=appid, player_steamid=steamid)
            top_msg = top_msg_query.order_by(TopMessages.timestamp.desc()).first()

            # Get top player
            top_player_query = Games.query.filter_by(appid=appid).first()

            if not top_player_query:
                db_session.add(Games(appid, steamid))
                db_session.commit()

            # Redo the query just in case there wasn't a top_player before
            top_player_query = Games.query.filter_by(appid=appid).first()
            top_player_id = top_player_query.top_player_steamid

            # Get top player's summaries
            get_top_player = "http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"

            if 'top_player_summaries' not in session:
                with urllib.request.urlopen(
                        f"{get_top_player}/?key={app.config['SECRET_KEY']}&steamids={top_player_id}") as top_player_url:
                    session.update(top_player_summaries=json.load(top_player_url)['response']['players'][0])

            # Get current player's playtime to crosscheck with top
            get_owned_games = "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
            args = f"appids_filter[0]={appid}&include_appinfo=true&format=json"

            if f'player_owned_game_{appid}' not in session:
                with urllib.request.urlopen(
                        f"{get_owned_games}/?key={app.config['SECRET_KEY']}&steamid={steamid}&{args}") as url:
                    session.update({f'player_owned_game{appid}': json.load(url)['response']['games'][0]})

            # Check if current player is top player
            if Games.query.filter_by(appid=appid).first():
                if f'top_player_owned_game_{appid}' not in session:
                    with urllib.request.urlopen(
                            f"{get_owned_games}/?key={app.config['SECRET_KEY']}&steamid={top_player_id}&{args}") as top_player_url:
                        session.update(
                            {f'top_player_owned_game_{appid}': json.load(top_player_url)['response']['games'][0]})

                session['top_player_summaries'].update(
                    playtime=session[f'top_player_owned_game_{appid}']['playtime_forever'])

                if int(session[f'top_player_owned_game_{appid}']['playtime_forever']) < int(
                        session[f'player_owned_game{appid}']['playtime_forever']):
                    # Substitute on the table
                    print("\nDeleting...")
                    [db_session.delete(q) for q in Games.query.filter_by(appid=appid).all()]

                    db_session.add(Games(appid=appid, top_player_steamid=steamid))
                    db_session.commit()
            else:
                db_session.add(Games(appid=appid, top_player_steamid=steamid))
                db_session.commit()

            # Get player's notes
            player_notes = GamesNotes.query.filter_by(appid=appid,
                                                      player_steamid=steamid).first() if not None else ''

            # Get game news
            get_news_for_app = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2"

            if f'steam_news_{appid}' not in session:
                with urllib.request.urlopen(f"{get_news_for_app}/?appid={appid}") as news_url:
                    session.update({f'steam_news_{appid}': json.load(news_url)['appnews']['newsitems']})

            to_remove = []
            for news in session.get(f'steam_news_{appid}', []):
                if news['feed_type'] == 1:
                    try:
                        news['contents'] = parser.format(news['contents'].format(
                            STEAM_CLAN_IMAGE='https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/clans'))

                    except KeyError:
                        to_remove.append(news)
                        continue

                    except IndexError as ie:
                        print(IndexError, ie)
                        to_remove.append(news)
                        continue

                [session[f'steam_news_{appid}'].remove(n) for n in to_remove]

            return render_template('game.html',
                                   steamid=steamid,
                                   player_notes=player_notes,
                                   top_player=session.get('top_player_summaries', {}),
                                   top_msg=top_msg,
                                   appid=appid,
                                   game=session.get(f'player_owned_game{appid}', {}),
                                   news_data=session.get(f'steam_news_{appid}', {}))

    return redirect('/')


@app.route('/update_playtime', methods=['GET', 'POST'])
def update_playtime():
    if request.method == 'POST':
        playtime = request.form.get('playtime')
        if playtime is not None:
            session.update(min_game_time=int(request.form.get('playtime')))

    return redirect('/games_index')


@app.route('/post_motd', methods=['POST'])
def post_motd():
    if request.method == 'POST':
        motd = request.form.get('motd', None)
        appid = request.form.get('appid', None)

        if motd and appid:
            steamid = session['openid'].split('/')[-1]

            # Check last timestamp
            can_post = False
            timestamp = TopMessages.query.filter_by(appid=appid).first()

            if not timestamp:
                can_post = True
            else:
                if time.time() - timestamp.timestamp > app.config['TIME_GAP']:
                    can_post = True

            if can_post:
                # Enough time has passed, allow user to post
                db_session.add(TopMessages(appid=appid, player_steamid=steamid, msg=motd, timestamp=time.time()))
                db_session.commit()

        return redirect(f'/game?appid={appid}')


@app.route('/save_note', methods=['POST'])
def save_note():
    if request.method == 'POST':
        steamid = int(session['openid'].split('/')[-1])
        note = request.form.get('note', None)
        appid = request.form.get('appid', None)

        if note and appid:
            # Delete old note
            [db_session.delete(q) for q in
             GamesNotes.query.filter_by(appid=appid, player_steamid=steamid).all()]

            db_session.add(GamesNotes(appid=appid, player_steamid=steamid, msg=note))
            db_session.commit()

        return redirect(f'/game?appid={appid}')


if __name__ == '__main__':
    init_db()
    app.run(debug=False, host='0.0.0.0')
