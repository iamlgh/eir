# models.py
from datetime import datetime, timezone, time, timedelta
from mongoengine import (
    Document,
    #     EmbeddedDocument,
    StringField,
    ListField,
    #     ReferenceField,
    #     IntField,
    DateTimeField,
    #     EmbeddedDocumentField,
    BooleanField,
    DictField,
)


class Team(Document):
    # In Conventus group/gruppe is the field for team/hold
    meta = {
        'collection': 'teams',
        'indexes': [
            {'fields': ['expires'], 'expireAfterSeconds': 0},
        ],
    }
    team_id = StringField(unique=True, required=True)  # Stores the ID from Conventus, e.g. "12345678"
    ref = StringField(required=True)  # Stores the reference, e.g. "Hold 31"
    name = StringField(required=True)  # Stores the name, e.g. "Hold 31 - Trampolin - Begyndere"
    start_date = DateTimeField(required=True)  # Stores the start date, e.g. "2026-09-13" (as datetime object, not string)
    end_date = DateTimeField(required=True)  # Stores the end date, e.g. "2027-05-30"
    season = StringField(required=True)  # Stores the season, e.g. "26/27"
    schedule = ListField(DictField())  # Stores the schedule, e.g. [{'day': 0, 'time': '18:30-21:00', 'place': 'GIC hal 3'}]
    pairing = StringField()  # (optional) Stores the pairing name, e.g. "Team 1 and 2"
    club_id = StringField(required=True)  # Stores the club ID from Conventus, e.g. "123"
    cancellations = ListField(StringField())  # Stores cancellations for the team, e.g. ["2024-10-01", "2024-10-08"], users won't be able to
    # check-out for a day that has a cancellation (they will get a message)
    expires = DateTimeField()  # date where the document will be automatically deleted from the db (no date, means no automatic deletion)

    @property
    def active(self) -> bool:
        return self.start_date <= datetime.now(self.start_date.tzinfo) <= self.end_date


class Event(Document):
    meta = {'collection': 'events'}
    title = StringField(required=True, max_length=200)
    start = DateTimeField(required=True)
    end = DateTimeField()
    description = StringField()
    location = StringField()
    tags = ListField(StringField(), default=lambda: ['all'])

    def save(self, *args, **kwargs) -> 'Event':
        if self.end is None and self.start is not None:
            if self.start.time() == time(0, 0, 0):
                self.end = self.start + timedelta(days=1)
            else:
                self.end = self.start + timedelta(hours=2.5)
        return super(Event, self).save(*args, **kwargs)


class Notification(Document):
    meta = {'collection': 'fb_notifications'}
    sid = StringField(required=True)  # Stores the service id for the user, i.e. the PSID from Facebook, the user_id from Telegram, e.g. "12345"
    service = StringField(required=True)  # Stores the messaging service e.g. "Facebook", "Telegram" for the user, e.g. "12345"
    team_ids = ListField(StringField())  # Stores the team IDs from Conventus, e.g. ["123457"]
    username = StringField(required=True)  # Stores the coach's username from Conventus, e.g. "iamacoach"
    # dept_id = StringField(required=True)  # Stores the dept ID from Conventus, e.g. "1234", not "a_1234"
    club_id = StringField(required=True)  # Stores the club ID from Conventus, e.g. "123"
    lingua = StringField(default='da')  # e.g., "en", "da"
    link_date = DateTimeField(default=lambda: datetime.now(timezone.utc))  # the datetime the notification was created, e.g. "2024-10-01 16:00:00"
    updated_at = DateTimeField()  # save hooks to automatically update the "updated_at" field whenever the document is saved

    def save(self, *args, **kwargs) -> 'Notification':
        self.updated_at = datetime.now(timezone.utc)
        return super(Notification, self).save(*args, **kwargs)


class PendingLink(Document):
    meta = {
        'collection': 'pending_links',
        'indexes': [
            # Automatically delete the document 10 minutes (600 seconds) after creation
            {'fields': ['created_at'], 'expireAfterSeconds': 600},
            # Index the code for lightning-fast lookups when entered in the UI
            'code',
        ],
    }

    code = StringField(required=True, min_length=6, max_length=6)  # e.g., "482910"
    sid = StringField(required=True)  # e.g., "123456"
    name = StringField()  # e.g., "Jane"
    service = StringField(required=True)  # e.g., "facebook", "telegram", "manychat"
    lingua = StringField(default='da')  # e.g., "en", "da"
    created_at = DateTimeField(default=lambda: datetime.now(timezone.utc))


class Signout(Document):
    meta = {'collection': 'signouts'}
    club_id = StringField(required=True)  # Stores the club ID from Conventus, e.g. "123"
    dept_id = StringField(
        required=True
    )  # Stores the dept ID from Conventus, e.g. "1234", not "a_1234", since the "a" prefix just indicates that it's a dept
    team_id = StringField(
        required=True
    )  # Stores the team ID from Conventus, e.g. "123456", not "g_123456", since the "g" prefix just indicates that it's a team
    member_id = StringField(required=True)  # Stores the member ID from Conventus, e.g. "1234567"
    date = DateTimeField(required=True)  # Stores the date of the event being signed out, time is always 00:00:00, e.g. "2024-10-01 00:00:00"
    status = BooleanField(required=True)  # signed-out True or False (if False, it means the user is signing back in)
    reason = StringField()  # Optional field for the user to provide more details about the reason for the signout, e.g. "sick", "vacation", "other"
    updated_at = DateTimeField()
    updated_by = StringField()  # Stores the name/ID of the member or coach that added or updated this

    # save hooks to automatically update the "updated_at" field whenever the document is saved
    def save(self, *args, **kwargs) -> 'Signout':
        self.updated_at = datetime.now(timezone.utc)
        return super(Signout, self).save(*args, **kwargs)
