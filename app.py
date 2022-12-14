import datetime
import json
import logging
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

app.config.update(
    SECRET_KEY=os.getenv('STEAM_API_KEY'),
    MAX_LEN_FLEXMSG=280,  # Length of a tweet
    MAX_LEN_NOTE=5000,
    SESSION_TYPE='filesystem',
    SESSION_PERMANENT=False,
    DEFAULT_MIN_GAME_TIME=300,
)

if os.getenv('ENV') == 'dev':
    from dotenv import load_dotenv

    load_dotenv()

    app.config.update(
        DATABASE_URI="postgresql://postgres:987654321@localhost/marcelo-project",
        TIME_GAP=30,  # 30 seconds
        TEMPLATES_AUTO_RELOAD=True,
    )

    app.logger.setLevel(logging.DEBUG)

else:
    # Fix for SQLALchemy 1.4.x not connecting to Heroku Postgres
    uri = os.getenv('DATABASE_URL')
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)

    app.config.update(
        DATABASE_URI=uri,
        TIME_GAP=86400,  # 1 day in seconds
    )

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
db_session = scoped_session(sessionmaker(autocommit=True,
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
    appid = Column(BigInteger)
    player_steamid = Column(BigInteger)
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
    appid = Column(BigInteger)
    player_steamid = Column(BigInteger)
    msg = Column(String(app.config['MAX_LEN_FLEXMSG']))
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

    return redirect('/games_index')


@app.route('/logout')
def logout():
    session.clear()
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

            # Don't allow user to flex by default, we'll check this before loading the page
            can_flex = False

            # Get current player's playtime
            get_owned_games = "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
            args = f"appids_filter[0]={appid}&include_appinfo=true&format=json"

            if f'player_owned_game_{appid}' not in session:
                with urllib.request.urlopen(
                        f"{get_owned_games}/?key={app.config['SECRET_KEY']}&steamid={steamid}&{args}") as url:
                    session.update({f'player_owned_game_{appid}': json.load(url)['response']['games'][0]})

            # Get top msg (FLEX)
            top_msg_query = TopMessages.query.filter_by(appid=appid)
            top_msg = top_msg_query.order_by(TopMessages.timestamp.desc()).first()

            # Get top player
            top_player_query = Games.query.filter_by(appid=appid).first()
            top_player_id = None

            if top_player_query:
                top_player_id = top_player_query.top_player_steamid

                # Get top player's summaries
                get_top_player = "http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"

                if f'top_player_summaries_{top_player_id}' not in session:
                    with urllib.request.urlopen(
                            f"{get_top_player}/?key={app.config['SECRET_KEY']}&steamids={top_player_id}") as top_player_url:
                        session.update(
                            {f'top_player_summaries_{top_player_id}': json.load(top_player_url)['response']['players'][
                                0]})

                # Check if current player is top player
                if f'top_player_owned_game_{appid}' not in session:
                    with urllib.request.urlopen(
                            f"{get_owned_games}/?key={app.config['SECRET_KEY']}&steamid={top_player_id}&{args}") as top_player_url:
                        session.update(
                            {f'top_player_owned_game_{appid}': json.load(top_player_url)['response']['games'][0]})

                session[f'top_player_summaries_{top_player_id}'].update(
                    {f'playtime_{appid}': session[f'top_player_owned_game_{appid}']['playtime_forever']})

                if int(session[f'top_player_owned_game_{appid}']['playtime_forever']) < int(
                        session[f'player_owned_game_{appid}']['playtime_forever']):
                    # Allow them to flex
                    can_flex = True

                elif top_player_id == steamid:
                    can_flex = True

            else:
                can_flex = True

            # Get player's notes
            player_notes = GamesNotes.query.filter_by(appid=appid,
                                                      player_steamid=steamid).first() if not None else ''

            # Get game news
            get_news_for_app = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2"

            if f'steam_news_{appid}' not in session:
                with urllib.request.urlopen(f"{get_news_for_app}/?appid={appid}") as news_url:
                    session.update({f'steam_news_{appid}': json.load(news_url)['appnews']['newsitems']})

            to_remove = []
            for i, news in enumerate(session.get(f'steam_news_{appid}', [])):
                if '.ru' in news['url']:
                    to_remove.append(news['gid'])
                    continue

                if news['feed_type'] == 1:
                    try:
                        parsed_contents = parser.format(news['contents']).format(
                            STEAM_CLAN_IMAGE='https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/clans')
                        session[f'steam_news_{appid}'][i]['parsed_contents'] = parsed_contents

                    except KeyError:
                        to_remove.append(news['gid'])
                        continue

                    except IndexError as ie:
                        print(IndexError, ie)
                        to_remove.append(news['gid'])
                        continue

                session[f'steam_news_{appid}'] = [n for n in session[f'steam_news_{appid}'] if
                                                  n['gid'] not in to_remove]

            return render_template('game.html',
                                   steamid=steamid,
                                   can_flex=can_flex,
                                   player_notes=player_notes,
                                   top_player=session.get(f'top_player_summaries_{top_player_id}', {}),
                                   top_msg=top_msg,
                                   appid=appid,
                                   game=session.get(f'player_owned_game_{appid}', {}),
                                   news_data=session.get(f'steam_news_{appid}', {}))

    return redirect('/')


@app.route('/update_playtime', methods=['GET', 'POST'])
def update_playtime():
    if request.method == 'POST':
        playtime = request.form.get('playtime')
        if playtime is not None:
            session.update(min_game_time=int(request.form.get('playtime')))

    return redirect('/games_index')


@app.route('/post_flexmsg', methods=['POST'])
def post_flexmsg():
    if 'openid' not in session:
        return redirect('/login')

    if request.method == 'POST':
        steamid = int(session['openid'].split('/')[-1])
        flexmsg = request.form.get('flexmsg', None)
        appid = request.form.get('appid', None)

        if not flexmsg and not appid:
            return redirect(f'/game?appid={appid}')

        # Check last timestamp
        timestamp = TopMessages.query.filter_by(appid=appid).first()

        can_post = False

        if not timestamp:
            can_post = True

        elif time.time() - timestamp.timestamp >= app.config['TIME_GAP']:
            # Enough time has passed, get info about current top player
            top_player_query = Games.query.filter_by(appid=appid).first()

            # Get current player's playtime to crosscheck with top
            get_owned_games = "http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
            args = f"appids_filter[0]={appid}&include_appinfo=true&format=json"

            if not top_player_query:
                can_post = True

            else:
                # There is a previous top player, check their data against current player
                top_player_id = top_player_query.top_player_steamid

                if f'top_player_owned_game_{appid}' not in session:
                    with urllib.request.urlopen(
                            f"{get_owned_games}/?key={app.config['SECRET_KEY']}&steamid={top_player_id}&{args}") as top_player_url:
                        session.update(
                            {f'top_player_owned_game_{appid}': json.load(top_player_url)['response']['games'][0]})

                session[f'top_player_summaries_{top_player_id}'].update(
                    {f'playtime_{appid}': session[f'top_player_owned_game_{appid}']['playtime_forever']})

                # Check if current player is top player
                if int(session[f'top_player_owned_game_{appid}']['playtime_forever']) <= int(
                        session[f'player_owned_game_{appid}']['playtime_forever']):
                    # They are, substitute on the Games table
                    can_post = True

        if can_post:
            [db_session.delete(q) for q in Games.query.filter_by(appid=appid).all()]
            Games.query.filter_by(appid=appid).all()  # Need to do this to refresh DB
            db_session.add(Games(appid=appid, top_player_steamid=steamid))
            db_session.add(TopMessages(appid=appid, player_steamid=steamid, msg=flexmsg, timestamp=time.time()))
            TopMessages.query.filter_by(appid=appid).all()  # Need to do this to refresh DB

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

        return redirect(f'/game?appid={appid}')


if __name__ == '__main__':
    init_db()
    app.run(debug=False, host='0.0.0.0')
