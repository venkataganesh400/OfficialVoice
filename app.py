import os
import random
import string
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload, backref
from sqlalchemy import or_, and_, asc
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from flask_moment import Moment

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_very_secret_and_hard_to_guess_key_for_dev')
socketio = SocketIO(app)
moment = Moment(app)

basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(basedir, 'static/uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(basedir, 'app.db'))
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

online_users = {}

followers = db.Table('followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'))
)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    voter_id = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    is_admin = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='active', nullable=False)
    date_joined = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    points = db.Column(db.Integer, default=0, nullable=False)
    bio = db.Column(db.Text, nullable=True)
    profile_image_url = db.Column(db.String(255), nullable=True, default=None)
    deletion_date = db.Column(db.DateTime, nullable=True)
    polls = db.relationship('Poll', backref='author', lazy='dynamic', cascade="all, delete-orphan")
    notifications_received = db.relationship('Notification', backref='recipient', lazy='dynamic', foreign_keys='Notification.recipient_id', cascade="all, delete-orphan")
    followed = db.relationship('User', secondary=followers, primaryjoin=(followers.c.follower_id == id), secondaryjoin=(followers.c.followed_id == id), backref=db.backref('followers', lazy='dynamic'), lazy='dynamic')

    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)
    def follow(self, user):
        if not self.is_following(user): self.followed.append(user)
    def unfollow(self, user):
        if self.is_following(user): self.followed.remove(user)
    def is_following(self, user): return self.followed.filter(followers.c.followed_id == user.id).count() > 0

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_one_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_two_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_message_timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    user_one = db.relationship('User', foreign_keys=[user_one_id])
    user_two = db.relationship('User', foreign_keys=[user_two_id])
    messages = db.relationship('Message', backref='conversation', lazy='dynamic', cascade="all, delete-orphan")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=True) # For poll chats
    author = db.relationship('User', backref='messages')


class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date_created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(20), default='approved')
    options = db.relationship('PollOption', backref='poll', lazy='subquery', cascade="all, delete-orphan")
    votes = db.relationship('Vote', backref='poll', lazy='dynamic', cascade="all, delete-orphan")
    likes = db.relationship('PollLike', backref='poll', lazy='dynamic', cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='poll', lazy='dynamic', cascade="all, delete-orphan")
    chat_messages = db.relationship('Message', backref='poll_chat', lazy='dynamic', cascade="all, delete-orphan")


class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(200), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    votes = db.relationship('Vote', backref='option', lazy='dynamic', cascade="all, delete-orphan")

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_option.id'), nullable=False)

class PollLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    author = db.relationship('User', backref='comments')
    parent_id = db.Column(db.Integer, db.ForeignKey('comment.id'))
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy='subquery', cascade="all, delete-orphan")
    likes = db.relationship('CommentLike', backref='comment', lazy='subquery', cascade="all, delete-orphan")

class CommentLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=False)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'))
    timestamp = db.Column(db.DateTime, index=True, default=lambda: datetime.now(timezone.utc))
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    actor = db.relationship('User', foreign_keys=[actor_id], backref='notifications_sent')
    poll = db.relationship('Poll', backref=backref('notifications', lazy='dynamic', cascade='all, delete-orphan'))

def award_points(user, points_to_add):
    if user: user.points = (user.points or 0) + points_to_add

def generate_voter_id(first_name, last_name):
    base = (first_name[:3] + last_name[:1]).upper().replace(' ', '');
    while True:
        random_suffix = ''.join(random.choices(string.digits, k=4)); voter_id = f"{base}{random_suffix}"
        if not User.query.filter_by(voter_id=voter_id).first(): return voter_id

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    g.user = db.session.get(User, user_id) if user_id else None
    if g.user and g.user.status != 'active':
        if request.endpoint not in ['login', 'logout', 'static']:
            session.clear()
            g.user = None

@app.context_processor
def inject_globals():
    unread_notifications_count = 0
    if g.user and g.user.status == 'active':
        unread_notifications_count = Notification.query.filter_by(recipient_id=g.user.id, is_read=False).count()
    return {'user': g.user, 'year': datetime.now(timezone.utc).year, 'unread_notifications_count': unread_notifications_count, 'online_users_list': list(online_users.keys())}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None: flash("You must be logged in to access this page.", "warning"); return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user or not g.user.is_admin: flash("You do not have permission for this.", "danger"); return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    featured_polls = Poll.query.options(joinedload(Poll.author)).order_by(Poll.date_created.desc()).limit(3).all()
    new_user = request.args.get('new_user', 'false').lower() == 'true'
    return render_template("Home.html", featured_polls=featured_polls, new_user=new_user)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if g.user: return redirect(url_for('home'))
    if request.method == 'POST':
        first_name=request.form.get('first_name'); last_name=request.form.get('last_name')
        email=request.form.get('email'); password=request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('This email is already registered.', 'warning'); return redirect(url_for('signup'))
        voter_id = generate_voter_id(first_name, last_name); new_user = User(first_name=first_name, last_name=last_name, email=email, voter_id=voter_id, points=10)
        new_user.set_password(password); db.session.add(new_user); db.session.commit()
        session['user_id'] = new_user.id; return redirect(url_for('home', new_user='true'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user: return redirect(url_for('home'))
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('identifier')).first()
        if user and user.check_password(request.form.get('password')):
            if user.status == 'deletion_pending':
                user.status = 'active'; user.deletion_date = None; db.session.commit()
                flash("Welcome back! Your account deletion has been cancelled.", "success")
            else: flash('Logged in successfully!', 'success')
            session['user_id'] = user.id
            next_url = request.args.get('next') or url_for('public_profile', voter_id=user.voter_id)
            return redirect(next_url)
        else: flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); flash("You have been logged out.", "success"); return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard(): return redirect(url_for('public_profile', voter_id=g.user.voter_id))

@app.route('/user/<string:voter_id>')
def public_profile(voter_id):
    user = User.query.filter_by(voter_id=voter_id, status='active').first_or_404()
    user_polls = user.polls.order_by(Poll.date_created.desc()).all()
    return render_template('public_profile.html', profile_user=user, polls=user_polls)

@app.route('/user/<string:voter_id>/followers')
def followers_list(voter_id):
    user = User.query.filter_by(voter_id=voter_id, status='active').first_or_404()
    return render_template('followers.html', user=user, user_list=user.followers, list_type="Followers")

@app.route('/user/<string:voter_id>/following')
def following_list(voter_id):
    user = User.query.filter_by(voter_id=voter_id, status='active').first_or_404()
    return render_template('followers.html', user=user, user_list=user.followed, list_type="Following")

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        g.user.first_name = request.form.get('first_name')
        g.user.last_name = request.form.get('last_name')
        g.user.bio = request.form.get('bio')
        db.session.commit()
        flash("Profile information updated.", "success")
        return redirect(url_for('public_profile', voter_id=g.user.voter_id))
    return render_template('edit_profile.html')

@app.route('/profile/upload-image', methods=['POST'])
@login_required
def upload_profile_image():
    if 'profile_image' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400
    file = request.files['profile_image']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"user_{g.user.id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{os.path.splitext(filename)[1]}"
        if g.user.profile_image_url:
            try:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(g.user.profile_image_url))
                if os.path.exists(old_path):
                    os.remove(old_path)
            except Exception as e:
                print(f"Error removing old profile picture: {e}")
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(save_path)
        g.user.profile_image_url = url_for('static', filename=f'uploads/{unique_filename}')
        db.session.commit()
        return jsonify({'success': True, 'new_url': g.user.profile_image_url})
    return jsonify({'success': False, 'error': 'File type not allowed'}), 400

@app.route('/profile/remove-image', methods=['POST'])
@login_required
def remove_profile_image():
    if g.user.profile_image_url:
        try:
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(g.user.profile_image_url))
            if os.path.exists(old_path):
                os.remove(old_path)
        except Exception as e:
            print(f"Error removing profile picture file: {e}")
        g.user.profile_image_url = None
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'No profile picture to remove'}), 400

@app.route('/inbox')
@login_required
def inbox():
    conversations = db.session.query(Conversation).filter(or_(Conversation.user_one_id == g.user.id, Conversation.user_two_id == g.user.id)).order_by(Conversation.last_message_timestamp.desc()).all()
    return render_template('inbox.html', conversations=conversations, conversation_id=None)

@app.route('/messages/user/<int:user_id>')
@login_required
def start_or_get_conversation(user_id):
    other_user = db.session.get(User, user_id)
    if not other_user:
        return "User not found", 404
        
    if user_id == g.user.id:
        flash("You cannot message yourself.", "warning")
        return redirect(url_for('inbox'))
    
    if not (g.user.is_following(other_user) and other_user.is_following(g.user)):
        flash("You must mutually follow a user to message them.", "warning")
        return redirect(url_for('public_profile', voter_id=other_user.voter_id))

    conversation = db.session.query(Conversation).filter(or_(and_(Conversation.user_one_id == g.user.id, Conversation.user_two_id == user_id), and_(Conversation.user_one_id == user_id, Conversation.user_two_id == g.user.id))).first()
    if not conversation:
        conversation = Conversation(user_one_id=g.user.id, user_two_id=user_id)
        db.session.add(conversation)
        db.session.commit()
    
    all_conversations = db.session.query(Conversation).filter(or_(Conversation.user_one_id == g.user.id, Conversation.user_two_id == g.user.id)).order_by(Conversation.last_message_timestamp.desc()).all()
    
    # Load message history
    messages = conversation.messages.options(joinedload(Message.author)).order_by(asc(Message.timestamp)).all()

    room_id = f"conversation_{conversation.id}"
    return render_template('messages.html', other_user=other_user, room_id=room_id, conversation_id=conversation.id, conversations=all_conversations, messages=messages)

@app.route('/poll/<int:poll_id>/chat')
@login_required
def poll_chat(poll_id):
    poll = db.session.get(Poll, poll_id)
    if not poll:
        return "Poll not found", 404
    has_voted = Vote.query.filter_by(user_id=g.user.id, poll_id=poll.id).first()
    if not has_voted and poll.user_id != g.user.id:
        flash("You must vote on this poll to join the community chat.", "warning")
        return redirect(url_for('poll_view', poll_id=poll.id))
        
    # Load message history
    messages = poll.chat_messages.options(joinedload(Message.author)).order_by(asc(Message.timestamp)).all()

    room_id = f"poll_chat_{poll.id}"
    voter_users = User.query.join(Vote).filter(Vote.poll_id == poll.id).all()
    participants = list({*voter_users, poll.author})
    return render_template('poll_chat.html', poll=poll, room_id=room_id, participants=participants, messages=messages)

@app.route('/polls/all')
def all_polls():
    polls = Poll.query.options(joinedload(Poll.author)).order_by(Poll.date_created.desc()).all()
    return render_template('all_polls.html', polls=polls)

@app.route('/polls/create', methods=['GET', 'POST'])
@login_required
def create_poll():
    if request.method == 'POST':
        topic = request.form.get('topic'); options = [opt.strip() for opt in request.form.getlist('options') if opt.strip()]
        if len(options) < 2:
            flash("You must provide at least two options.", 'warning'); return render_template('create_poll.html', topic=topic, description=request.form.get('description'))
        new_poll = Poll(topic=topic, description=request.form.get('description'), author=g.user);
        db.session.add(new_poll); award_points(g.user, 5)
        for option_text in options: db.session.add(PollOption(text=option_text, poll=new_poll))
        db.session.commit(); flash('Poll published!', 'success'); return redirect(url_for('poll_view', poll_id=new_poll.id))
    return render_template('create_poll.html')

@app.route('/poll/<int:poll_id>')
def poll_view(poll_id):
    poll = db.session.get(Poll, poll_id)
    if not poll:
        return "Poll not found", 404
    user_vote = user_like = None
    user_has_voted = False
    if g.user:
        user_vote = Vote.query.filter_by(user_id=g.user.id, poll_id=poll.id).first()
        user_like = PollLike.query.filter_by(user_id=g.user.id, poll_id=poll.id).first()
        if user_vote: user_has_voted = True
    comments = poll.comments.filter_by(parent_id=None).options(joinedload(Comment.author)).order_by(Comment.timestamp.asc()).all()
    user_comment_likes = [cl.comment_id for cl in CommentLike.query.filter_by(user_id=g.user.id).all()] if g.user else []
    return render_template('poll_view.html', poll=poll, user_vote=user_vote, total_votes=poll.votes.count(), user_has_liked=bool(user_like), comments=comments, user_comment_likes=user_comment_likes, user_has_voted=user_has_voted)

@app.route('/poll/<int:poll_id>/delete', methods=['GET', 'POST'])
@login_required
def delete_poll(poll_id):
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.author.id != g.user.id:
        flash("You cannot delete this poll.", "danger"); return redirect(url_for('all_polls'))
    if request.method == 'POST':
        if not g.user.check_password(request.form.get('password')):
            flash("Incorrect password.", "danger"); return redirect(url_for('delete_poll', poll_id=poll.id))
        award_points(g.user, -5); db.session.delete(poll); db.session.commit()
        flash("Poll has been deleted.", "success"); return redirect(url_for('all_polls'))
    return render_template('delete_poll.html', poll=poll)

@app.route('/poll/vote/<int:poll_id>', methods=['POST'])
@login_required
def poll_vote_action(poll_id):
    poll = db.session.get(Poll, poll_id)
    option_id = request.form.get('option')
    if not option_id: return jsonify({'success': False, 'error': 'You must select an option.'}), 400
    if Vote.query.filter_by(user_id=g.user.id, poll_id=poll.id).first(): return jsonify({'success': False, 'error': 'You have already voted on this poll.'}), 400
    db.session.add(Vote(user_id=g.user.id, poll_id=poll.id, option_id=option_id)); award_points(g.user, 1); award_points(poll.author, 1)
    if poll.user_id != g.user.id: db.session.add(Notification(recipient_id=poll.user_id, actor_id=g.user.id, action='vote', poll_id=poll.id))
    db.session.commit()
    user_vote = Vote.query.filter_by(user_id=g.user.id, poll_id=poll.id).first()
    total_votes = poll.votes.count()
    results_html = render_template('_poll_results.html', poll=poll, user_vote=user_vote, total_votes=total_votes)
    return jsonify({'success': True, 'message': 'Vote recorded!', 'results_html': results_html})

@app.route('/poll/like/<int:poll_id>', methods=['POST'])
@login_required
def like_poll_action(poll_id):
    poll = db.session.get(Poll, poll_id)
    like = PollLike.query.filter_by(user_id=g.user.id, poll_id=poll.id).first()
    if like:
        db.session.delete(like)
        liked = False
    else:
        db.session.add(PollLike(user_id=g.user.id, poll_id=poll.id))
        liked = True
        award_points(g.user, 1)
        if poll.user_id != g.user.id: db.session.add(Notification(recipient_id=poll.user_id, actor_id=g.user.id, action='like', poll_id=poll.id))
    db.session.commit()
    return jsonify({'liked': liked, 'count': poll.likes.count()})

@app.route('/poll/comment/<int:poll_id>', methods=['POST'])
@login_required
def comment_on_poll(poll_id):
    poll=db.session.get(Poll, poll_id)
    comment_text=request.form.get('comment_text','').strip()
    parent_id = request.form.get('parent_id')
    if not comment_text: return jsonify({'success': False, 'error': 'Comment cannot be empty.'}), 400
    comment=Comment(text=comment_text, poll_id=poll_id, user_id=g.user.id, parent_id=parent_id if parent_id else None)
    db.session.add(comment); award_points(g.user, 2); award_points(poll.author, 1)
    if parent_id:
        parent_comment = db.session.get(Comment, parent_id)
        if parent_comment and parent_comment.author.id != g.user.id: db.session.add(Notification(recipient_id=parent_comment.author.id, actor_id=g.user.id, action='reply', poll_id=poll.id))
    elif poll.user_id != g.user.id: db.session.add(Notification(recipient_id=poll.user_id, actor_id=g.user.id, action='comment', poll_id=poll.id))
    db.session.commit()
    user_comment_likes = [cl.comment_id for cl in CommentLike.query.filter_by(user_id=g.user.id).all()] if g.user else []
    template = '_reply.html' if parent_id else '_comment.html'
    html = render_template(template, comment=comment, user_comment_likes=user_comment_likes)
    return jsonify({'success': True, 'comment_html': html, 'parent_id': parent_id, 'total_comments': poll.comments.count()})

@app.route('/comment/like/<int:comment_id>', methods=['POST'])
@login_required
def like_comment_action(comment_id):
    comment=db.session.get(Comment, comment_id)
    like=CommentLike.query.filter_by(user_id=g.user.id, comment_id=comment_id).first()
    if like:
        db.session.delete(like)
        liked = False
    else:
        db.session.add(CommentLike(user_id=g.user.id, comment_id=comment_id))
        liked = True
        award_points(g.user, 1)
        if comment.user_id != g.user.id: db.session.add(Notification(recipient_id=comment.user_id, actor_id=g.user.id, action='like_comment', poll_id=comment.poll_id))
    db.session.commit()
    return jsonify({'liked': liked, 'count': len(comment.likes)})

@app.route('/user/follow/<int:user_id>', methods=['POST'])
@login_required
def follow_user(user_id):
    user_to_follow=db.session.get(User, user_id)
    if not user_to_follow or user_to_follow.id == g.user.id:
        return jsonify({'error': 'Invalid user.'}), 400
    action = ''
    if g.user.is_following(user_to_follow):
        g.user.unfollow(user_to_follow)
        action = 'unfollowed'
    else:
        g.user.follow(user_to_follow)
        action = 'followed'
        db.session.add(Notification(recipient_id=user_id, actor_id=g.user.id, action='follow', poll_id=None))
    db.session.commit()
    return jsonify({'action': action, 'follower_count': user_to_follow.followers.count()})

@app.route('/notifications')
@login_required
def notifications():
    unread_notifications = g.user.notifications_received.filter_by(is_read=False).all()
    for notif in unread_notifications: notif.is_read = True
    db.session.commit()
    all_user_notifications=g.user.notifications_received.options(joinedload(Notification.actor), joinedload(Notification.poll)).order_by(Notification.timestamp.desc()).all()
    return render_template('notifications.html', notifications=all_user_notifications)

@app.route('/delete_account', methods=['GET', 'POST'])
@login_required
def delete_account():
    if request.method == 'POST':
        if not g.user.check_password(request.form.get('password')):
            flash("Incorrect password.", 'danger')
        elif len(request.form.get('reason', '')) < 10:
            flash("Reason must be at least 10 characters.", "warning")
        else:
            g.user.status='deletion_pending'
            g.user.deletion_date = datetime.now(timezone.utc) + timedelta(days=7)
            db.session.commit()
            session.clear()
            flash('Account scheduled for deletion in 7 days.', 'success')
            return redirect(url_for('home'))
    return render_template('delete_account.html')

@app.route('/admin')
@admin_required
def admin_dashboard():
    stats={'active_users':User.query.filter_by(status='active').count(),'total_polls':Poll.query.count(),'pending_deletion':User.query.filter_by(status='deletion_pending').count()}
    return render_template('admin/admin_dashboard.html', stats=stats)

@app.route('/admin/users')
@admin_required
def manage_users():
    users=User.query.order_by(User.id).all()
    return render_template('admin/manage_users.html', users=users)

# === THIS FUNCTION IS THE FIX ===
@app.route('/admin/user/delete/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('manage_users'))

    if user.is_admin or user.id == g.user.id:
        flash("Admin accounts and your own account cannot be deleted from here.", "danger")
        return redirect(url_for('manage_users'))

    try:
        # 1. Clean up things not directly cascaded from the User object
        # -----------------------------------------------------------------

        # A) Manual cleanup of the 'followers' many-to-many table
        db.session.execute(followers.delete().where(followers.c.follower_id == user_id))
        db.session.execute(followers.delete().where(followers.c.followed_id == user_id))
        
        # B) Manual cleanup of all direct private messages (this triggers the message cascade)
        conversations_to_delete = Conversation.query.filter(or_(Conversation.user_one_id == user_id, Conversation.user_two_id == user_id)).all()
        for conv in conversations_to_delete:
            db.session.delete(conv)

        # C) Manually delete user's votes, likes, and comments on OTHER people's polls.
        #    The user's own polls (and their comments/votes) will be deleted by the cascade from User->Poll.
        Vote.query.filter_by(user_id=user_id).delete(synchronize_session='fetch')
        PollLike.query.filter_by(user_id=user_id).delete(synchronize_session='fetch')
        CommentLike.query.filter_by(user_id=user_id).delete(synchronize_session='fetch')
        
        # We need to fetch and delete comments to trigger their cascade rules (for replies, etc.)
        user_comments = Comment.query.filter_by(user_id=user_id).all()
        for comment in user_comments:
            db.session.delete(comment)
        
        # 2. Finally, delete the user object.
        # ------------------------------------
        # The cascade="all, delete-orphan" on `user.polls` and `user.notifications_received`
        # will now automatically handle deleting:
        #   - The user's polls.
        #   - All votes, likes, comments, and poll chat messages associated with THOSE polls.
        #   - All notifications received by the user.
        
        db.session.delete(user)
        db.session.commit()
        
        flash(f"User '{user.email}' and all their associated data have been permanently dismissed.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred while deleting the user: {e}", "danger")
        print(f"Error dismissing user {user_id}: {e}")

    return redirect(url_for('manage_users'))

# --- SOCKET.IO HANDLERS ---
@socketio.on('connect')
def handle_connect():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    if not user or user.status != 'active':
        disconnect()
        return
    online_users[user.id] = request.sid
    emit('user_status_change', {'user_id': user.id, 'status': 'online'}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    if user and user.id in online_users:
        del online_users[user.id]
        emit('user_status_change', {'user_id': user.id, 'status': 'offline'}, broadcast=True)

@socketio.on('join_room')
def handle_join_room(data):
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    room_id = data.get('room')
    if user and room_id:
        join_room(room_id)

@socketio.on('leave_room')
def handle_leave_room(data):
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    room_id = data.get('room')
    if user and room_id:
        leave_room(room_id)

@socketio.on('send_message')
def handle_send_message(data):
    user_id = session.get('user_id')
    user = db.session.get(User, user_id)
    room = data.get('room')
    message_text = data.get('text', '').strip()

    if not (user and message_text and room):
        return

    new_message_db = None
    if room.startswith('conversation_'):
        try:
            conversation_id = int(room.split('_')[1])
            conversation = db.session.get(Conversation, conversation_id)
            if conversation and user.id in [conversation.user_one_id, conversation.user_two_id]:
                new_message_db = Message(text=message_text, user_id=user.id, conversation_id=conversation_id)
                conversation.last_message_timestamp = datetime.now(timezone.utc)
        except (ValueError, IndexError):
            return

    elif room.startswith('poll_chat_'):
        try:
            poll_id = int(room.split('_')[2])
            poll = db.session.get(Poll, poll_id)
            if poll:
                new_message_db = Message(text=message_text, user_id=user.id, poll_id=poll_id)
        except (ValueError, IndexError):
            return
            
    if new_message_db:
        db.session.add(new_message_db)
        db.session.commit()

        message_for_emit = {
            'text': new_message_db.text,
            'author_name': user.first_name,
            'author_id': user.id,
            'timestamp': new_message_db.timestamp.isoformat()
        }
        emit('receive_message', message_for_emit, room=room)
    else:
        # Handle error case where room is invalid
        emit('error', {'message': "Invalid chat session."})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)
