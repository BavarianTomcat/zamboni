import calendar
import time

from django.conf import settings
from django.http import Http404

import commonware.log
import django_filters
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.generics import GenericAPIView
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

import amo
from amo.helpers import absolutify, urlparams
from amo.urlresolvers import reverse
from amo.utils import send_mail_jinja
from lib.cef_loggers import app_pay_cef
from market.models import Price
from stats.models import ClientData, Contribution

from mkt.api.authentication import (RestAnonymousAuthentication,
                                    RestOAuthAuthentication,
                                    RestSharedSecretAuthentication)
from mkt.api.authorization import (AllowOwner, AllowReadOnly, AnyOf,
                                   GroupPermission)
from mkt.api.base import CORSMixin, MarketplaceView
from mkt.webpay.forms import FailureForm, PrepareWebAppForm, PrepareInAppForm
from mkt.webpay.models import ProductIcon
from mkt.webpay.serializers import PriceSerializer, ProductIconSerializer
from mkt.webpay.webpay_jwt import (get_product_jwt, InAppProduct,
                                   sign_webpay_jwt, WebAppProduct)

from . import tasks


log = commonware.log.getLogger('z.webpay')


class PreparePayWebAppView(CORSMixin, MarketplaceView, GenericAPIView):
    authentication_classes = [RestOAuthAuthentication,
                              RestSharedSecretAuthentication]
    permission_classes = [IsAuthenticated]
    cors_allowed_methods = ['post']

    def post(self, request, *args, **kwargs):
        form = PrepareWebAppForm(request.DATA)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        app = form.cleaned_data['app']

        region = getattr(request, 'REGION', None)
        if region and region.id not in app.get_price_region_ids():
            log.info('Region {0} is not in {1}'
                     .format(region.id, app.get_price_region_ids()))
            return Response('Payments are limited and flag not enabled',
                            status=status.HTTP_403_FORBIDDEN)

        if app.is_premium() and app.has_purchased(request._request.amo_user):
            log.info('Already purchased: {0}'.format(app.pk))
            return Response({'reason': u'Already purchased app.'},
                            status=status.HTTP_409_CONFLICT)

        app_pay_cef.log(request._request, 'Preparing JWT', 'preparing_jwt',
                        'Preparing JWT for: {0}'.format(app.pk), severity=3)

        token = get_product_jwt(
            WebAppProduct(app),
            client_data=ClientData.get_or_create(request._request),
            lang=request._request.LANG,
            region=request._request.REGION,
            source=request._request.REQUEST.get('src', ''),
            user=request._request.amo_user,
        )

        return Response(token, status=status.HTTP_201_CREATED)


class PreparePayInAppView(CORSMixin, MarketplaceView, GenericAPIView):
    authentication_classes = []
    permission_classes = []
    cors_allowed_methods = ['post']

    def post(self, request, *args, **kwargs):
        form = PrepareInAppForm(request.DATA)
        if not form.is_valid():
            app_pay_cef.log(
                request._request,
                'Preparing InApp JWT Failed',
                'preparing_inapp_jwt_failed',
                'Preparing InApp JWT Failed error: {0}'.format(form.errors),
                severity=3
            )
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        inapp = form.cleaned_data['inapp']

        app_pay_cef.log(
            request._request,
            'Preparing InApp JWT',
            'preparing_inapp_jwt',
            'Preparing InApp JWT for: {0}'.format(inapp.pk), severity=3
        )

        token = get_product_jwt(
            InAppProduct(inapp),
            client_data=ClientData.get_or_create(request._request),
            lang=request._request.LANG,
            source=request._request.REQUEST.get('src', ''),
        )

        return Response(token, status=status.HTTP_201_CREATED)


class StatusPayView(CORSMixin, MarketplaceView, GenericAPIView):
    authentication_classes = [RestOAuthAuthentication,
                              RestSharedSecretAuthentication]
    permission_classes = [AllowOwner]
    cors_allowed_methods = ['get']
    queryset = Contribution.objects.filter(type=amo.CONTRIB_PURCHASE)
    lookup_field = 'uuid'

    def get_object(self):
        try:
            obj = super(StatusPayView, self).get_object()
        except Http404:
            # Anything that's not correct will be raised as a 404 so that it's
            # harder to iterate over contribution values.
            log.info('Contribution not found')
            return None

        if not obj.addon.has_purchased(self.request.amo_user):
            log.info('Not in AddonPurchase table')
            return None

        return obj

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        data = {'status': 'complete' if self.object else 'incomplete'}
        return Response(data)


class PriceFilter(django_filters.FilterSet):
    pricePoint = django_filters.CharFilter(name="name")

    class Meta:
        model = Price
        fields = ['pricePoint']


class PricesViewSet(MarketplaceView, CORSMixin, ListModelMixin,
                    RetrieveModelMixin, GenericViewSet):
    queryset = Price.objects.filter(active=True).order_by('price')
    serializer_class = PriceSerializer
    cors_allowed_methods = ['get']
    authentication_classes = [RestAnonymousAuthentication]
    permission_classes = [AllowAny]
    filter_class = PriceFilter


class FailureNotificationView(MarketplaceView, GenericAPIView):
    authentication_classes = [RestOAuthAuthentication,
                              RestSharedSecretAuthentication]
    permission_classes = [GroupPermission('Transaction', 'NotifyFailure')]
    queryset = Contribution.objects.filter(uuid__isnull=False)

    def patch(self, request, *args, **kwargs):
        form = FailureForm(request.DATA)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        obj = self.get_object()
        data = {
            'transaction_id': obj,
            'transaction_url': absolutify(
                urlparams(reverse('mkt.developers.transactions'),
                          transaction_id=obj.uuid)),
            'url': form.cleaned_data['url'],
            'retries': form.cleaned_data['attempts']}
        owners = obj.addon.authors.values_list('email', flat=True)
        send_mail_jinja('Payment notification failure.',
                        'webpay/failure.txt',
                        data, recipient_list=owners)
        return Response(status=status.HTTP_202_ACCEPTED)


class ProductIconViewSet(CORSMixin, MarketplaceView, ListModelMixin,
                         RetrieveModelMixin, GenericViewSet):
    authentication_classes = [RestOAuthAuthentication,
                              RestSharedSecretAuthentication,
                              RestAnonymousAuthentication]
    permission_classes = [AnyOf(AllowReadOnly,
                                GroupPermission('ProductIcon', 'Create'))]
    queryset = ProductIcon.objects.all()
    serializer_class = ProductIconSerializer
    cors_allowed_methods = ['get', 'post']
    filter_fields = ('ext_url', 'ext_size', 'size')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.DATA)
        if serializer.is_valid():
            log.info('Resizing product icon %s @ %s to %s for webpay' % (
                serializer.data['ext_url'],
                serializer.data['ext_size'],
                serializer.data['size']))
            tasks.fetch_product_icon.delay(serializer.data['ext_url'],
                                           serializer.data['ext_size'],
                                           serializer.data['size'])
            return Response(serializer.data, status=status.HTTP_202_ACCEPTED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes((AllowAny,))
def sig_check(request):
    """
    Returns a signed JWT to use for signature checking.

    This is for Nagios checks to ensure that Marketplace's
    signed tokens are valid when processed by Webpay.
    """
    issued_at = calendar.timegm(time.gmtime())
    req = {
        'iss': settings.APP_PURCHASE_KEY,
        'typ': settings.SIG_CHECK_TYP,
        'aud': settings.APP_PURCHASE_AUD,
        'iat': issued_at,
        'exp': issued_at + 3600,  # expires in 1 hour
        'request': {}
    }
    return Response({'sig_check_jwt': sign_webpay_jwt(req)},
                    status=201)
