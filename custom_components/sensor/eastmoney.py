'''
# Module name:
    eastmoney.py
# Prerequisite:
    Based on Python 3.4
    Need python module requests and bs4
# Purpose:
    Fund sensor powered by Eastmoney
# Author:
    Retroposter retroposter@outlook.com
# Created:
    Aug.31th 2017
'''

import logging
from datetime import timedelta

from bs4 import BeautifulSoup
from requests.exceptions import ConnectionError as ConnectError, HTTPError, Timeout
import requests
import re
import voluptuous as vol

from homeassistant.const import (CONF_LATITUDE, CONF_LONGITUDE, CONF_API_KEY, CONF_MONITORED_CONDITIONS, CONF_NAME, TEMP_CELSIUS, ATTR_ATTRIBUTION)
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
import homeassistant.helpers.config_validation as cv


_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Fund'
ATTRIBUTION = 'Powered by East Money'

PAT_DATE = re.compile(r'\d{4}-\d{1,2}-\d{1,2}')

CONF_UPDATE_INTERVAL = 'update_interval'
CONF_NAME = 'name'
CONF_FUND_ID = 'fund_id'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_FUND_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_UPDATE_INTERVAL, default=timedelta(minutes=15)): (vol.All(cv.time_period, cv.positive_timedelta)),
})

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Fund sensor.""" 
    fund_id = config[CONF_FUND_ID]
    name = config[CONF_NAME]
    interval = config.get(CONF_UPDATE_INTERVAL)
    fund_data = EastmoneyData(fund_id, interval)
    fund_data.update()
    # If connection failed don't setup platform.
    if fund_data.data is None:
        return False

    sensors = [EastmoneySensor(fund_data, name)]
    add_devices(sensors, True)

class EastmoneySensor(Entity):
    def __init__(self, fund_data, name):
        """Initialize the sensor."""
        self.fund_data = fund_data
        self.client_name = name
        self._state = None
        self._trend = -1

    @property
    def name(self):
        """Return the name of the sensor."""
        return self.client_name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        if self._trend == 1:
            return 'mdi:trending-up'
        if self._trend == -1:
            return 'mdi:trending-down'
        return 'mdi:trending-neutral'

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        attrs = {}
        if self.fund_data.data is None:
            attrs[ATTR_ATTRIBUTION] = ATTRIBUTION
            return attrs

        attrs[ATTR_ATTRIBUTION] = '{0} {1}'.format(self.fund_data.data['last_update'], ATTRIBUTION)
        est_nav = self.fund_data.data['est_nav']
        attrs['est growth'] = est_nav['enav_growth']
        attrs['est growth rate'] = est_nav['enav_rate']
        attrs['recent 1 month'] = est_nav['rct_1month']
        attrs['recent 1 year'] = est_nav['rct_1year']
        nav = self.fund_data.data['nav']
        attrs['last trading day'] = nav['nav_date']
        attrs['last nav'] = nav['nav']
        attrs['last growth rate'] = nav['nav_rate']        
        return attrs

    def update(self):
        """Get the latest data from He Weather and updates the states."""
        self.fund_data.update()
        if self.fund_data.data is None:
            return
        est_nav = self.fund_data.data['est_nav']
        self._state = est_nav['enav']
        growth = float(est_nav['enav_growth'])
        if growth > 0:
            self._trend = 1
        elif growth < 0:
            self._trend = -1
        else:
            self._trend = 0

class EastmoneyData(object):
    """Get the latest data from Eastmoney."""

    def __init__(self, fund_id, internal):
        self.fund_id = fund_id
        self.data = None
        # Apply throttling to methods using configured interval
        self.update = Throttle(internal)(self._update)

    def _update(self):
        """Get the latest data from Eastmoney."""

        url = 'http://fund.eastmoney.com/{0}.html?spm=aladin'.format(self.fund_id)
        resp = None
        try:
            resp = requests.get(url)
        except (ConnectError, HTTPError, Timeout, ValueError) as error:
            _LOGGER.error("Unable to connect to Eastmoney. %s", error)
            return

        soup = BeautifulSoup(resp.text, 'html.parser')

        tit = self._get_fund_tit(soup)
        if tit is not None and len(tit) == 2 and tit[1] == self.fund_id:
            self.data = self._analyze(soup)
        else:
            _LOGGER.error('Invalid fund id: %s.', self.fund_id)

    def _get_fund_tit(self, soup):
        fund_tit = soup.find('div', class_='fundDetail-tit')
        if fund_tit is None:
            return
        tit = fund_tit.find('div')
        if tit is None:
            return
        return tit.text.split('(')

    def _analyze(self, soup):
        fund_info_item = soup.find('div', class_='fundInfoItem')
        if fund_info_item is None:
            _LOGGER.error('Element \'div,class_=fundInfoItem\' not found.')
            return
        fund_data = fund_info_item.find('div', class_='dataOfFund')
        if fund_data is None:
            _LOGGER.error('Element \'div,class_=dataOfFund\' not found.')
            return
        data_item_01 = fund_data.find('dl', class_='dataItem01')
        data_item_02 = fund_data.find('dl', class_='dataItem02')
        # Until now, I do not care accnav.
        # data_item_03 = fund_data.find('dl', class_='dataItem03')
        if data_item_01 is None or data_item_02 is None:
            _LOGGER.error('Element \'div,class_=dataItem01|dataItem02\' not found.')
            return
        est_nav = self._get_estnav(data_item_01)
        nav = self._get_nav(data_item_02)
        if est_nav is None or nav is None:
            return None
        return {'est_nav': est_nav, 'nav': nav, 'last_update': est_nav['enav_time']}

    def _get_estnav(self, estnav_data):
        nav_time = estnav_data.find('span', id='gz_gztime')
        dds = estnav_data.find_all('dd')
        if dds is None or len(dds) != 3:
            _LOGGER.error('Element \'dd\' error.')
            return None
        nav = dds[0].find('span', id='gz_gsz')
        nav_growth = dds[0].find('span', id='gz_gszze')
        nav_rate = dds[0].find('span', id='gz_gszzl')
        rct_1month = dds[1].find('span', class_='ui-font-middle ui-color-green ui-num')
        if rct_1month is None:
            rct_1month = dds[1].find('span', class_='ui-font-middle ui-color-red ui-num')
        rct_1year = dds[2].find('span', class_='ui-font-middle ui-color-green ui-num')
        if rct_1year is None:
            rct_1year = dds[2].find('span', class_='ui-font-middle ui-color-red ui-num')
        if nav_time is not None:
            nav_time = nav_time.text.lstrip('(').rstrip(')')
        if nav is not None:
            nav = nav.text
        if nav_growth is not None:
            nav_growth = nav_growth.text
        if nav_rate is not None:
            nav_rate = nav_rate.text
        if rct_1month is not None:
            rct_1month = rct_1month.text
        if rct_1year is not None:
            rct_1year = rct_1year.text
        if nav is None or nav_time is None:
            return None
        # Correct the growth
        if float(nav_rate[0:-1]) < 0:
            nav_growth = '-' + nav_growth
        return {'enav_time': nav_time, 'enav': nav, 'enav_growth': nav_growth, 'enav_rate': nav_rate, 'rct_1month': rct_1month, 'rct_1year': rct_1year}

    def _get_nav(self, nav_data):
        date = nav_data.find('dt')
        dds = nav_data.find_all('dd')
        if dds is None or len(dds) != 3:
            _LOGGER.error('Element \'dd\' error.')
            return None
        nav = dds[0].find('span', class_='ui-font-large ui-color-green ui-num')
        if nav is None:
            nav = dds[0].find('span', class_='ui-font-large ui-color-red ui-num')
        nav_rate = dds[0].find('span', class_='ui-font-middle ui-color-green ui-num')
        if nav_rate is None:
            nav_rate = dds[0].find('span', class_='ui-font-middle ui-color-red ui-num')
        rct_3month = dds[1].find('span', class_='ui-font-middle ui-color-green ui-num')
        rct_3year = dds[2].find('span', class_='ui-font-middle ui-color-red ui-num')
        if date is not None:
            date = re.findall(PAT_DATE, date.text)[0]
        if nav is not None:
            nav = nav.text
        if nav_rate is not None:
            nav_rate = nav_rate.text
        if rct_3month is not None:
            rct_3month = rct_3month.text
        if rct_3year is not None:
            rct_3year = rct_3year.text
        if nav is None or date is None:
            return None     
        return {'nav_date': date, 'nav': nav, 'nav_rate': nav_rate, 'rct_3month': rct_3month, 'rct_3year': rct_3year}