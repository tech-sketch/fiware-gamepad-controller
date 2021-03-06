# -*- coding: utf-8 -*-
import os
import ssl
import time
import signal
from datetime import datetime, timezone
from logging import getLogger

import pygame
from pygame.locals import JOYBUTTONDOWN, JOYBUTTONUP, JOYHATMOTION, JOYAXISMOTION

import paho.mqtt.client as mqtt

from src.utils import find_item

logger = getLogger(__name__)

TOPIC_KEY = 'controller'


class ControllerError(Exception):
    def __init__(self, *args, **kwargs):
        self.cause = kwargs.pop('cause') if 'cause' in kwargs else None
        super().__init__(*args, **kwargs)


class Controller:
    def __init__(self, conf):
        try:
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            pygame.init()
            pygame.display.init()
            pygame.joystick.init()
            controller = pygame.joystick.Joystick(0)
            controller.init()

            self.__conf = conf
            self.__button_items = dict()
            self.__hat_items = dict()
            self.__topics = dict()

            self._is_stop = False

            logger.info('initialized %s', conf.name)
        except pygame.error as e:
            raise ControllerError('init error', cause=e)

        self.__mqtt_client = None

    def connect(self):
        def __on_connect(client, userdata, flags, response_code):
            logger.info('connected mqtt broker[%s:%d], response_code=%d',
                        self.__conf.mqtt.host, self.__conf.mqtt.port, response_code)

        def __on_disconnect(client, userdata, response_code):
            logger.info('disconnected mqtt broker, response_code=%d', response_code)

        self.__mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)
        self.__mqtt_client.on_connect = __on_connect
        self.__mqtt_client.on_disconnect = __on_disconnect

        if 'cafile' in self.__conf.mqtt and os.path.isfile(self.__conf.mqtt.cafile):
            self.__mqtt_client.tls_set(self.__conf.mqtt.cafile, tls_version=ssl.PROTOCOL_TLSv1_2)
        if 'username' in self.__conf.mqtt and 'password' in self.__conf.mqtt:
            self.__mqtt_client.username_pw_set(self.__conf.mqtt.username, self.__conf.mqtt.password)

        self.__mqtt_client.connect(self.__conf.mqtt.host, port=self.__conf.mqtt.port, keepalive=60)
        self.__mqtt_client.loop_start()
        return self

    def __find_button_item(self, button_id):
        if button_id not in self.__button_items:
            item = find_item(self.__conf.controller.buttons, lambda item: item.key == button_id)
            self.__button_items[button_id] = item
        return self.__button_items[button_id]

    def __find_hat_item(self, hat_id):
        if hat_id not in self.__hat_items:
            item = find_item(self.__conf.controller.hats,
                             lambda item: item.x == hat_id[0] and item.y == hat_id[1])
            self.__hat_items[hat_id] = item
        return self.__hat_items[hat_id]

    def __find_topic(self, topic_id):
        if topic_id not in self.__topics:
            item = find_item(self.__conf.mqtt.topics, lambda item: item.key == topic_id)
            self.__topics[topic_id] = item
        return self.__topics[topic_id]

    def describe_events(self):
        logger.info('start describing...')

        def callback(event):
            if event.type == JOYBUTTONDOWN:
                logger.info('Button down event, event.button=%s', event.button)
            elif event.type == JOYBUTTONUP:
                logger.info('Button up event, event.button=%s', event.button)
            elif event.type == JOYHATMOTION:
                logger.info('Hat event, event.hat=%s, event.value=%s', event.hat, event.value)
            elif event.type == JOYAXISMOTION:
                logger.info('Axis event, event.axis=%s, event.value=%s', event.axis, event.value)
        self.__subscribe_events(callback)
        logger.info('stop describing...')

    def publish_events(self):
        logger.info('start publishing...')

        def callback(event):
            now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f%z')
            if event.type == JOYBUTTONDOWN and self.__find_button_item(event.button):
                self.__publish_mqtt(f'{now}|button|{self.__find_button_item(event.button).value}')
            elif event.type == JOYHATMOTION and self.__find_hat_item(event.value):
                self.__publish_mqtt(f'{now}|button|{self.__find_hat_item(event.value).value}')
            else:
                logger.debug('ignore event, %s', str(event))
        self.__subscribe_events(callback)

        logger.info('stop publishing...')

    def __publish_mqtt(self, payload):
        topic = self.__find_topic(TOPIC_KEY)
        if topic:
            self.__mqtt_client.publish(topic.value, payload)
            logger.info('published "%s" to "%s"', payload, topic.value)
        else:
            logger.warning('no topic found, key=%s', TOPIC_KEY)

    def __subscribe_events(self, callback):
        try:
            signal.signal(signal.SIGINT, self.__stop_loop)
            signal.signal(signal.SIGTERM, self.__stop_loop)
        except ValueError:
            pass

        try:
            while not self._is_stop:
                for event in pygame.event.get():
                    callback(event)
                time.sleep(0.1)
        except pygame.error as e:
            raise ControllerError('subscribe event error', cause=e)
        finally:
            if self.__mqtt_client is not None:
                self.__mqtt_client.loop_stop()
                self.__mqtt_client.disconnect()

    def __stop_loop(self, signal, frame):
        self._is_stop = True
        logger.info('stop main loop')
