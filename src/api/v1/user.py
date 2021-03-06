import json
import logging
import bcrypt
from email.utils import parseaddr

from tornado.web import HTTPError, RequestHandler

from models.user import UserProfile, User
from models.status import Status
from models.token import Token

from api.v1.base_handler import BaseHandler
from auth.token_auth import bearerAuth, userPassLogin, basicAuth

from utils.atomFeed import generate_feed

from managers.user_manager import new_user, UserManager
from managers.notification_manager import NotificationManager
from tasks.emails import confirm_token

from settings import salt_code

from tasks.emails import send_password_reset
from tasks.timelines import *


log = logging.getLogger(__name__)


class AuthUser(BaseHandler):

    @basicAuth
    async def get(self, user):

        if user.is_remote:
            resp.status = falcon.HTTP_401
            resp.body = json.dumps({"Error": "Remote user"})

        token = Token.create(user=user)

        payload = {
            "token": "Bearer " + token.key
        }

        self.write(payload)
        self.set_status(200)


class UserHandler(BaseHandler):
    async def get(self, id):
        try:
            #person = await self.application.objects.get(User, User.id==int(id))
            profile = await self.application.objects.get(UserProfile, UserProfile.id==int(id))

            self.write(json.dumps(profile.to_json(), default=str).encode('utf-8'))
            self.set_status(200)
        except User.DoesNotExist:
            raise HTTPError(404, "User not found")

class VerifyCredentials(BaseHandler):

    @bearerAuth
    async def get(self, user):

        self.write(json.dumps(user.to_json(), default=str).encode('utf-8'))

class ProfileManager(BaseHandler):

    """
    
        Update the profile info for an user.

        patch: Valid arguments are:

            - display_name: The name to show
            - note: A description
            - locked: true if the account is private
            - bot: true if the account is a bot
            - avatar: a file containing the image for the user account

    """

    @bearerAuth
    async def patch(self, user):

        errors = []

        valids_types = ['image/png', 'image/jpeg']

        if self.get_argument('display_name', None):
            if len(self.get_argument('display_name')) <= 31: 
                user.name = self.get_argument('display_name')
            else:
                errors.append('display_name length exceeded.')
        
        if self.get_argument('note', False):
            if len(self.get_argument('note')) <= 160:
                user.description = self.get_argument('note')
            else:
                errors.append('Note length exceeded.')

        if self.get_argument('locked', False):
            user.private = self.get_argument('locked') in ['true']

        if self.get_argument('bot', False):
            user.bot = self.get_argument('bot') in ['true']

        if 'avatar' in self.request.files.keys():
            if self.request.files['avatar'][0]['content_type'] in valids_types:
            # TO DO 
            # Review that this works
                user.avatar_file = user.update_avatar(self.request.files['avatar'][0]['body'])

        
        # Once all the changes have been made

        await self.application.objects.update(user)
        self.write(json.dumps(user.to_json(), default=str).encode('utf-8'))
    

class LogoutUser(BaseHandler):

    """
        Delete the token associated to an account.
        Therefor this ends its session.
    """

    @bearerAuth
    async def post(sef, user):
        token = self.request.headers.get('Authorization').split()[1]

        try:
            token = await self.application.objects.get(Token, key=token)

            if user == token.user:
                await self.application.objects.delete(token)
                self.write({"Success": "Removed token"})
            else:
                self.set_status(401)
                self.write({"Error": "Unauthorized user"})
        
        except Token.DoesNotExist:
            self.write({"Error": "This token doesn't exists"})

class atomFeed(BaseHandler):

    async def get(self, id):

        try:
            user = await self.application.objects.get(User, id=id)
            if self.get_argument('max_id', False):
                feed = generate_feed(user, int(self.get_argument('max_id')))
            else:
                feed = generate_feed(user)
            
            self.set_header('Content-Type', 'application/xml')
            self.write(feed)  

        except User.DoesNotExist:
            self.set_status(404)
            self.write({"Error": "User doesn't exits"})

class UserURLConfirmation(BaseHandler):

    async def get(self, token):
        email = confirm_token(token)
        if email:
            try:
                user = self.application.objects.get(User, email=email)
                user.confirmed = True 
                self.application.objects.update(user)
            
            except User.DoesNotExist:
                self.set_status(404)
                self.write({"Error": "User not available"})
        else:
            self.set_status(500)
            self.write({"Error": "Invalid code or too old"})

class RegisterUser(BaseHandler):

    async def post(self):
        username = self.get_argument('username')
        password = self.get_argument('password')
        confirmation = self.get_argument('password_confirmation')
        email = self.get_argument('email')
        
        valid_password = password == confirmation

        if '@' not in parseaddr(email)[1]: 
            self.set_status(500)
            self.write({"Error": "Invalid email"})
            self.finish()
            return

        # TODO: Move the logic from here to elsewhere
        username_count = await self.application.objects.count(User.select().where(User.username==username))

        free = username_count == 0
        log.debug(f'username is free: {free}')
        if valid_password and free:

            try:
                log.debug("Creating new user")
                profile = new_user(
                    username = username, 
                    password = password,
                    email = parseaddr(email)[1]
                )

                if not profile:
                    log.error("Error creating profile")
                    self.set_status(402)
                    self.write({"Error": "Wrong username. Valid characters are number, ascii letters and (.) (_)"})
                else:
                    self.set_status(200)
                    self.write(json.dumps(profile.to_json(), default=str))

            except Exception as e:
                log.error(e)
                self.set_status(500)
                self.write({"Error": "Error creating new user"})
        
        else:

            self.set_status(400)
            self.write({"Error": "User not available or password not matching"})

class PasswordRecovery(BaseHandler):

    async def post(self):
        token = self.get_argument('token')
        data = confirm_token(token)
        if data:
            typ, email = data 
        else:
            self.write({"Error": "Code expired"})
            self.set_status(500)
            self.finish()
            return 
        
        if email:
            try:
                user = await self.application.objects.get(User, email=email)
                password = self.get_argument('password')
                confirmation = self.get_argument('password_confirmation')
                valid_password = password == confirmation

                if valid_password:
                    user.password = bcrypt.hashpw(password, salt_code) 
                    await self.application.objects.update(user)
                else:
                    self.set_status(402)
                    self.write({"Error": "Password and confirmation do not match"})
            
            except User.DoesNotExist:
                self.set_status(404)
                self.write({"Error": "User not available"})
        else:
            self.set_status(500)
            self.write({"Error": "Invalid code or too old"})

class RequestPasswordRecovery(BaseHandler):

    async def post(self, email):
        try:
            user = await self.application.objects.get(User, email=email)
            profile = user.profile.get()
            send_password_reset(profile)
        except User.DoesNotExist:
            pass 
        self.write({"Success": "If the email is our database we'll contact the user"})
        self.set_status(200)


class FollowUser(BaseHandler):

    @bearerAuth
    async def post(self, target_id, user):

        try:
            target = await self.application.objects.get(UserProfile, id=target_id)
            user.follow(target)
            add_to_timeline(user, target)
            NotificationManager(user).create_follow_notification(target)
            log.debug(f"{user.username} followed {target.username}")
        except User.DoesNotExist:
            self.write({"Error": "User not found"})
            self.set_status(400)

class UnFollowUser(BaseHandler):

    @bearerAuth
    async def post(self, target_id, user):

        try:
            target = await self.application.objects.get(UserProfile, id=target_id)
            user.unfollow(target)
            remove_from_timeline(user, target)
        except User.DoesNotExist:
            self.write({"Error": "User not found"})
            self.set_status(400)

class FetchFollowers(BaseHandler):

    async def get(self, id):
        try:
            user = await self.application.objects.get(UserProfile, id=id)
            followers = [follower.to_json() for follower in user.followers()]
            self.write(json.dumps(followers, default=str))
            self.set_status(200)
        except UserProfile.DoesNotExist:
            self.write({"Error": "user not found"})
            self.set_status(404)
            
class FetchFollowing(BaseHandler):

    async def get(self, id):
        try:
            user = await self.application.objects.get(UserProfile, id=id)
            followers = [following.to_json() for following in user.following()]
            self.write(json.dumps(followers, default=str))
            self.set_status(200)
        except UserProfile.DoesNotExist:
            self.write({"Error": "user not found"})
            self.set_status(404)
        except Exception as e:
            print(e)

class Relationship(BaseHandler):

    @bearerAuth
    async def get(self, user):
        target_id = self.get_argument('id', None)

        if target_id:
            try:
                target = await self.application.objects.get(UserProfile, id=target_id)
                User
                data = {
                    'id': target_id,
                }
                
                # Check if the current user is following that account

                manager = UserManager(user)
                target_manager = UserManager(target)

                data['following'] = manager.is_following(target)
                data['followed_by'] = target_manager.is_following(user)

                self.write(data)
                self.set_status(200)
            except UserProfile.DoesNotExist:
                log.error(f'User with id {target_id} not found')
                self.write({"Error": "Target user not found"})
                self.set_status(404)
        else:
            self.write({"Error": "User not provided"})
            self.set_status(422)
            log.warning("Missing target id on request")