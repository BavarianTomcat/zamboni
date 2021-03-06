# -*- coding: utf-8 -*-
import json

from django.core.urlresolvers import NoReverseMatch
from django.test.utils import override_settings

import mock
from nose.tools import eq_

import amo
from amo.tests import app_factory
from amo.urlresolvers import reverse
from amo.utils import urlparams

import mkt
from mkt.api.tests.test_oauth import RestOAuth
from mkt.webapps.models import ContentRating, Geodata


class TestContentRating(amo.tests.TestCase):

    def setUp(self):
        self.app = app_factory()

    def test_get_content_ratings(self):
        for body in (mkt.ratingsbodies.CLASSIND, mkt.ratingsbodies.ESRB):
            ContentRating.objects.create(addon=self.app, ratings_body=body.id,
                                         rating=0)
        res = self.client.get(reverse('content-ratings-list',
                                      args=[self.app.app_slug]))
        eq_(res.status_code, 200)

        res = json.loads(res.content)
        eq_(len(res['objects']), 2)
        rating = res['objects'][0]
        eq_(rating['body'], 'classind')
        eq_(rating['rating'], '0')

    def test_get_content_ratings_since(self):
        cr = ContentRating.objects.create(addon=self.app, ratings_body=0,
                                          rating=0)
        cr.update(modified=self.days_ago(100))

        res = self.client.get(urlparams(
            reverse('content-ratings-list', args=[self.app.app_slug]),
            since=self.days_ago(5)))
        eq_(res.status_code, 404)

        cr.update(modified=self.days_ago(1))
        res = self.client.get(urlparams(
            reverse('content-ratings-list', args=[self.app.id]),
            since=self.days_ago(5)))
        eq_(res.status_code, 200)
        eq_(len(json.loads(res.content)['objects']), 1)

    def test_view_whitelist(self):
        """Only -list, no create/update/delete."""
        with self.assertRaises(NoReverseMatch):
            reverse('content-ratings-create', args=[self.app.id])
        with self.assertRaises(NoReverseMatch):
            reverse('content-ratings-update', args=[self.app.id])
        with self.assertRaises(NoReverseMatch):
            reverse('content-ratings-delete', args=[self.app.id])
        reverse('content-ratings-list', args=[self.app.app_slug])


@override_settings(SECRET_KEY='test')
class TestContentRatingPingback(RestOAuth):

    def setUp(self):
        super(TestContentRatingPingback, self).setUp()
        self.app = app_factory(status=amo.STATUS_NULL)
        self.url = reverse('content-ratings-pingback', args=[self.app.pk])
        self.data = {
            'ROW': {
                'FIELD': [
                    {
                        'TYPE': 'int',
                        'NAME': 'rowId',
                        'VALUE': '1'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'token',
                        'VALUE': self.app.iarc_token()
                    },
                    {
                        'TYPE': 'int',
                        'NAME': 'submission_id',
                        'VALUE': '52'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'security_code',
                        'VALUE': 'AB12CD3'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'title',
                        'VALUE': 'Twitter'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'company',
                        'VALUE': 'Mozilla'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'platform',
                        'VALUE': 'Firefox'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'rating_PEGI',
                        'VALUE': '16+'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'descriptors_PEGI',
                        'VALUE': 'Language,Online'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'rating_USK',
                        'VALUE': 'Rating Refused'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'descriptors_USK',
                        'VALUE': u'Explizite Sprache'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'rating_ESRB',
                        'VALUE': 'Mature 17+'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'descriptors_ESRB',
                        'VALUE': 'Strong Language'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'rating_CLASSIND',
                        'VALUE': '14+'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'descriptors_CLASSIND',
                        'VALUE': u'Linguagem Impr\xF3pria,Cont\xE9udo Sexual'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'rating_Generic',
                        'VALUE': '16+'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'descriptors_Generic',
                        'VALUE': 'Language'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'storefront',
                        'VALUE': 'Mozilla'
                    },
                    {
                        'TYPE': 'string',
                        'NAME': 'interactive_elements',
                        'VALUE': 'Shares Info,Shares Location,Digital Purchases,Users Interact'
                    }
                ]
            }
        }

    def test_slug_url(self):
        url = reverse('content-ratings-pingback', args=[self.app.app_slug])
        res = self.anon.post(url, data=json.dumps(self.data))
        eq_(res.status_code, 200)

    def test_invalid_content_type(self):
        res = self.anon.post(self.url, data=json.dumps(self.data),
                             content_type='application/xml')
        eq_(res.status_code, 415)

        res = self.anon.post(self.url, data=json.dumps(self.data),
                             content_type='application/x-www-form-urlencoded')
        eq_(res.status_code, 415)

    @mock.patch('mkt.webapps.models.Webapp.details_complete')
    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data')
    def test_post_content_ratings_pingback(self, details_mock,
                                           storefront_mock):
        details_mock.return_value = True
        eq_(self.app.status, amo.STATUS_NULL)

        res = self.anon.post(self.url, data=json.dumps(self.data))
        eq_(res.status_code, 200)

        # Verify things were saved to the database.
        app = self.app.reload()

        # IARC info.
        eq_(app.iarc_info.submission_id, 52)
        eq_(app.iarc_info.security_code, 'AB12CD3')
        assert storefront_mock.called

        # Ratings.
        eq_(app.content_ratings.count(), 5)
        for rb, rating in [
            (mkt.ratingsbodies.CLASSIND, mkt.ratingsbodies.CLASSIND_14),
            (mkt.ratingsbodies.ESRB, mkt.ratingsbodies.ESRB_M),
            (mkt.ratingsbodies.GENERIC, mkt.ratingsbodies.GENERIC_16),
            (mkt.ratingsbodies.PEGI, mkt.ratingsbodies.PEGI_16),
            (mkt.ratingsbodies.USK, mkt.ratingsbodies.USK_REJECTED)]:
            eq_(app.content_ratings.get(ratings_body=rb.id).rating, rating.id,
                'Unexpected rating for rating body %s.' % rb)

        # Descriptors.
        self.assertSetEqual(
            app.rating_descriptors.to_keys(),
            ['has_classind_lang', 'has_classind_sex_content',
             'has_pegi_lang', 'has_pegi_online',
             'has_generic_lang',
             'has_esrb_strong_lang',
             'has_usk_lang'])

        # Interactives.
        self.assertSetEqual(
            app.rating_interactives.to_keys(),
            ['has_shares_info', 'has_shares_location',
             'has_digital_purchases', 'has_users_interact'])

        eq_(app.status, amo.STATUS_PENDING)
        assert app.current_version.nomination

    @override_settings(SECRET_KEY='foo')
    def test_token_mismatch(self):
        res = self.anon.post(self.url, data=json.dumps(self.data))
        eq_(res.status_code, 400)
        eq_(json.loads(res.content)['detail'], 'Token mismatch')

    @mock.patch('mkt.webapps.models.Webapp.details_complete')
    def test_post_content_ratings_pingback_iarc_exclude(self, details_mock):
        details_mock.return_value = True
        self.app._geodata.update(region_br_iarc_exclude=True,
                                 region_de_iarc_exclude=True)

        self.anon.post(self.url, data=json.dumps(self.data))

        # Verify things were saved to the database.
        geodata = Geodata.objects.get(addon=self.app)
        assert not geodata.region_br_iarc_exclude
        assert not geodata.region_de_iarc_exclude

    @mock.patch('mkt.developers.api.ContentRatingsPingback.verify_data')
    @mock.patch('mkt.webapps.models.Webapp.details_complete',
                new=mock.Mock())
    @mock.patch('mkt.webapps.models.Webapp.set_iarc_storefront_data',
                new=mock.Mock())
    def test_verify_data(self, verify_mock):
        verify_mock.return_value = False
        res = self.anon.post(self.url, data=json.dumps(self.data))
        eq_(res.status_code, 400)
