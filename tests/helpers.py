"""
    tests.helpers
    ~~~~~~~~~~~~~

    Provides helper functions for unit tests in this package.

    :copyright: 2012, 2013, 2014, 2015 Jeffrey Finkelstein
                <jeffrey.finkelstein@gmail.com> and contributors.
    :license: GNU AGPLv3+ or BSD

"""
import sys
is_python_version_2 = sys.version_info[0] == 2

if is_python_version_2:
    import types

    def isclass(obj):
        return isinstance(obj, (types.TypeType, types.ClassType))
else:
    def isclass(obj):
        return isinstance(obj, type)

import datetime
import functools
import uuid

from flask import Flask
from flask import json
try:
    from flask.ext import sqlalchemy as flask_sqlalchemy
except ImportError:
    has_flask_sqlalchemy = False
else:
    has_flask_sqlalchemy = True
from nose import SkipTest
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import create_engine
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import event
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import Interval
from sqlalchemy import select
from sqlalchemy import Time
from sqlalchemy import Unicode
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import backref
from sqlalchemy.orm import relationship
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session as SessionBase
from sqlalchemy.types import CHAR
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.associationproxy import association_proxy

from flask.ext.restless import APIManager
from flask.ext.restless import CONTENT_TYPE

dumps = json.dumps
loads = json.loads

#: The User-Agent string for Microsoft Internet Explorer 8.
#:
#: From <http://blogs.msdn.com/b/ie/archive/2008/02/21/the-internet-explorer-8-user-agent-string.aspx>.
MSIE8_UA = 'Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 6.0; Trident/4.0)'

#: The User-Agent string for Microsoft Internet Explorer 9.
#:
#: From <http://blogs.msdn.com/b/ie/archive/2010/03/23/introducing-ie9-s-user-agent-string.aspx>.
MSIE9_UA = 'Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0)'


def skip_unless(condition, reason=None):
    """Decorator that skips `test` unless `condition` is ``True``.

    This is a replacement for :func:`unittest.skipUnless` that works with
    ``nose``. The argument ``reason`` is a string describing why the test was
    skipped.

    This decorator can be applied to functions, methods, or classes.

    """
    def skip(test):
        message = 'Skipped {0}: {1}'.format(test.__name__, reason)

        if isclass(test):
            for attr, val in test.__dict__.items():
                if callable(val) and not attr.startswith('__'):
                    setattr(test, attr, skip(val))
            return test

        @functools.wraps(test)
        def inner(*args, **kw):
            if not condition:
                raise SkipTest(message)
            return test(*args, **kw)
        return inner

    return skip


def skip(reason=None):
    """Unconditionally skip a test.

    This is a convenience function for ``skip_unless(False, reason)``.

    """
    return skip_unless(False, reason)


def unregister_fsa_session_signals():
    """Unregisters Flask-SQLAlchemy session commit and rollback signal
    handlers.

    When a Flask-SQLAlchemy object is created, it registers signal handlers for
    ``before_commit``, ``after_commit``, and ``after_rollback`` signals. In
    case of using both a plain SQLAlchemy session and a Flask-SQLAlchemy
    session (as is happening in the tests in this package), we need to
    unregister handlers or there will be some exceptions during test
    executions like::

        AttributeError: 'Session' object has no attribute '_model_changes'

    """
    if not has_flask_sqlalchemy:
        return
    events = flask_sqlalchemy._SessionSignalEvents
    signal_names = ('before_commit', 'after_commit', 'after_rollback')
    for signal_name in signal_names:
        signal = getattr(events, 'session_signal_{0}'.format(signal_name))
        event.remove(SessionBase, signal)


def force_json_contenttype(test_client):
    """Ensures that all requests made by the specified Flask test client have
    the correct ``Content-Type`` header.

    For :http:method:`patch` requests, this means
    ``application/json-patch+json``. For all other requests, the content type
    is set to ``application/vnd.api+json``, unless another content type is
    explicitly specified at the time the method is invoked.

    """
    # Create a decorator for the test client request methods that adds
    # a JSON Content-Type by default if none is specified.
    def set_content_type(func, headers=None, content_type=CONTENT_TYPE):
        @functools.wraps(func)
        def new_func(*args, **kw):
            if 'content_type' not in kw:
                kw['content_type'] = content_type
            if 'headers' not in kw:
                kw['headers'] = dict()
            if 'Accept' not in kw['headers']:
                kw['headers']['Accept'] = CONTENT_TYPE
            return func(*args, **kw)
        return new_func

    for methodname in ('get', 'put', 'post', 'delete'):
        # Decorate the original test client request method.
        old_method = getattr(test_client, methodname)
        setattr(test_client, methodname, set_content_type(old_method))
    # # PATCH methods need to have `application/json-patch+json` content type.
    # test_client.patch = set_content_type(test_client.patch,
    #                                      'application/json-patch+json')


# This code adapted from
# http://docs.sqlalchemy.org/en/latest/core/custom_types.html#backend-agnostic-guid-type
class GUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses Postgresql's UUID type, otherwise uses CHAR(32), storing as
    stringified hex values.

    """
    impl = CHAR

    def load_dialect_impl(self, dialect):
        descriptor = UUID() if dialect.name == 'postgresql' else CHAR(32)
        return dialect.type_descriptor(descriptor)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return str(value)
        if not isinstance(value, uuid.UUID):
            return uuid.UUID(value).hex
        # If we get to this point, we assume `value` is a UUID object.
        return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return uuid.UUID(value)


class FlaskTestBase(object):
    """Base class for tests which use a Flask application.

    The Flask test client can be accessed at ``self.app``. The Flask
    application itself is accessible at ``self.flaskapp``.

    """

    def setUp(self):
        """Creates the Flask application and the APIManager."""
        # create the Flask application
        app = Flask(__name__)
        app.config['DEBUG'] = True
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        # This is required by `manager.url_for()` in order to construct
        # absolute URLs.
        app.config['SERVER_NAME'] = 'localhost'
        app.logger.disabled = True
        self.flaskapp = app

        # create the test client
        self.app = app.test_client()

        force_json_contenttype(self.app)


class DatabaseTestBase(FlaskTestBase):
    """Base class for tests that use a SQLAlchemy database.

    The :meth:`setUp` method does the necessary SQLAlchemy initialization, and
    the subclasses should populate the database with models and then create the
    database (by calling ``self.Base.metadata.create_all()``).

    """

    def setUp(self):
        """Initializes the components necessary for models in a SQLAlchemy
        database.

        """
        super(DatabaseTestBase, self).setUp()
        # initialize SQLAlchemy
        app = self.flaskapp
        engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'],
                               convert_unicode=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False,
                                    bind=engine)
        self.session = scoped_session(self.Session)
        self.Base = declarative_base()
        self.Base.metadata.bind = engine

    def tearDown(self):
        """Drops all tables from the temporary database."""
        self.session.remove()
        self.Base.metadata.drop_all()


class ManagerTestBase(DatabaseTestBase):
    """Base class for tests that use a SQLAlchemy database and an
    :class:`flask_restless.APIManager`.

    The :class:`flask_restless.APIManager` is accessible at ``self.manager``.

    """

    def setUp(self):
        """Initializes an instance of :class:`flask.ext.restless.APIManager`.

        """
        super(ManagerTestBase, self).setUp()
        self.manager = APIManager(self.flaskapp, session=self.session)


# class TestSupport(ManagerTestBase):
#     """Base class for test cases which use a database with some basic models.

#     """

#     def setUp(self):
#         """Creates some example models and creates the database tables.

#         This class defines a whole bunch of models with various properties for
#         use in testing, so look here first when writing new tests.

#         """
#         super(TestSupport, self).setUp()

#         # declare the models
#         class Program(self.Base):
#             __tablename__ = 'program'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode, unique=True)

#         class ComputerProgram(self.Base):
#             __tablename__ = 'computer_program'
#             computer_id = Column(Integer, ForeignKey('computer.id'),
#                                  primary_key=True)
#             program_id = Column(Integer, ForeignKey('program.id'),
#                                 primary_key=True)
#             licensed = Column(Boolean, default=False)
#             program = relationship('Program')

#         class Computer(self.Base):
#             __tablename__ = 'computer'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode, unique=True)
#             vendor = Column(Unicode)
#             buy_date = Column(DateTime)
#             owner_id = Column(Integer, ForeignKey('person.id'))
#             owner = relationship('Person')
#             programs = relationship('ComputerProgram',
#                                     cascade="all, delete-orphan",
#                                     backref='computer')

#             def speed(self):
#                 return 42

#             @property
#             def speed_property(self):
#                 return self.speed()

#         class Screen(self.Base):
#             __tablename__ = 'screen'
#             id = Column(Integer, primary_key=True)
#             width = Column(Integer, nullable=False)
#             height = Column(Integer, nullable=False)

#             @hybrid_property
#             def number_of_pixels(self):
#                 return self.width * self.height

#             @number_of_pixels.setter
#             def number_of_pixels(self, value):
#                 self.height = value / self.width

#         class Person(self.Base):
#             __tablename__ = 'person'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode, unique=True)
#             age = Column(Integer)
#             other = Column(Float)
#             birth_date = Column(Date)
#             computers = relationship('Computer')

#             @hybrid_property
#             def is_minor(self):
#                 if getattr(self, 'age') is None:
#                     return None
#                 return self.age < 18

#             @hybrid_property
#             def is_above_21(self):
#                 if getattr(self, 'age') is None:
#                     return None
#                 return self.age > 21

#             @is_above_21.expression
#             def is_above_21(cls):
#                 return select([cls.age > 21]).as_scalar()

#             def name_and_age(self):
#                 return "{0} (aged {1:d})".format(self.name, self.age)

#             def first_computer(self):
#                 return sorted(self.computers, key=lambda k: k.name)[0]

#         class LazyComputer(self.Base):
#             __tablename__ = 'lazycomputer'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode)
#             ownerid = Column(Integer, ForeignKey('lazyperson.id'))
#             owner = relationship('LazyPerson',
#                                  backref=backref('computers', lazy='dynamic'))

#         class LazyPerson(self.Base):
#             __tablename__ = 'lazyperson'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode)

#         class User(self.Base):
#             __tablename__ = 'user'
#             id = Column(Integer, primary_key=True)
#             email = Column(Unicode, primary_key=True)
#             wakeup = Column(Time)

#         class Planet(self.Base):
#             __tablename__ = 'planet'
#             name = Column(Unicode, primary_key=True)

#         class Satellite(self.Base):
#             __tablename__ = 'satellite'
#             name = Column(Unicode, primary_key=True)
#             period = Column(Interval, nullable=True)

#         class Star(self.Base):
#             __tablename__ = 'star'
#             id = Column("star_id", Integer, primary_key=True)
#             inception_time = Column(DateTime, nullable=True)

#         class Vehicle(self.Base):
#             __tablename__ = 'vehicle'
#             id = Column(GUID, primary_key=True)

#         class CarModel(self.Base):
#             __tablename__ = 'car_model'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode)
#             seats = Column(Integer)

#             manufacturer_id = Column(Integer,
#                                      ForeignKey('car_manufacturer.id'))
#             manufacturer = relationship('CarManufacturer')

#         class CarManufacturer(self.Base):
#             __tablename__ = 'car_manufacturer'
#             id = Column(Integer, primary_key=True)
#             name = Column(Unicode)
#             models = relationship('CarModel')

#         class Project(self.Base):
#             __tablename__ = 'project'
#             id = Column(Integer, primary_key=True)
#             person_id = Column(Integer, ForeignKey('person.id'))
#             person = relationship('Person',
#                                   backref=backref('projects', lazy='dynamic'))

#         class Proof(self.Base):
#             __tablename__ = 'proof'
#             id = Column(Integer, primary_key=True)
#             project = relationship('Project', backref=backref('proofs',
#                                                               lazy='dynamic'))
#             project_id = Column(Integer, ForeignKey('project.id'))
#             person = association_proxy('project', 'person')
#             person_id = association_proxy('project', 'person_id')

#         self.Person = Person
#         self.Program = Program
#         self.ComputerProgram = ComputerProgram
#         self.LazyComputer = LazyComputer
#         self.LazyPerson = LazyPerson
#         self.User = User
#         self.Computer = Computer
#         self.Planet = Planet
#         self.Satellite = Satellite
#         self.Star = Star
#         self.Vehicle = Vehicle
#         self.CarManufacturer = CarManufacturer
#         self.CarModel = CarModel
#         self.Project = Project
#         self.Proof = Proof
#         self.Screen = Screen

#         # create all the tables required for the models
#         self.Base.metadata.create_all()

#     def tearDown(self):
#         """Drops all tables from the temporary database."""
#         self.Base.metadata.drop_all()


# class TestSupportPrefilled(TestSupport):
#     """Base class for tests which use a database and have an
#     :class:`flask_restless.APIManager` with a :class:`flask.Flask` app object.

#     The test client for the :class:`flask.Flask` application is accessible to
#     test functions at ``self.app`` and the :class:`flask_restless.APIManager`
#     is accessible at ``self.manager``.

#     The database will be prepopulated with five ``Person`` objects. The list of
#     these objects can be accessed at ``self.people``.

#     """

#     def setUp(self):
#         """Creates the database, the Flask application, and the APIManager."""
#         # create the database
#         super(TestSupportPrefilled, self).setUp()
#         # create some people in the database for testing
#         lincoln = self.Person(name=u'Lincoln', age=23, other=22,
#                               birth_date=datetime.date(1900, 1, 2))
#         mary = self.Person(name=u'Mary', age=19, other=19)
#         lucy = self.Person(name=u'Lucy', age=25, other=20)
#         katy = self.Person(name=u'Katy', age=7, other=10)
#         john = self.Person(name=u'John', age=28, other=10)
#         self.people = [lincoln, mary, lucy, katy, john]
#         self.session.add_all(self.people)
#         self.session.commit()
