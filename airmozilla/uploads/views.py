import base64
import datetime
import hashlib
import hmac
import urllib
import os
import time

from django import http
from django.shortcuts import render
from django.template.defaultfilters import filesizeformat
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.conf import settings

import requests
from funfactory.urlresolvers import reverse
from slugify import slugify
from jsonview.decorators import json_view

from airmozilla.main.models import Event, SuggestedEvent
from .models import Upload


@login_required
def home(request):
    context = {}
    context['uploads'] = (
        Upload.objects
        .filter(user=request.user)
        .order_by('created')
    )

    return render(request, 'uploads/home.html', context)


@login_required
def upload(request):
    context = {}
    return render(request, 'uploads/upload.html', context)


@login_required
@json_view
def sign(request):
    AWS_ACCESS_KEY = settings.AWS_ACCESS_KEY_ID
    AWS_SECRET_KEY = settings.AWS_SECRET_ACCESS_KEY
    S3_UPLOAD_BUCKET = settings.S3_UPLOAD_BUCKET

    file_name = object_name = request.GET.get('s3_object_name')
    if not file_name:
        return http.HttpResponseBadRequest('Missing s3_object_name')
    mime_type = request.GET.get('s3_object_type')
    if not mime_type:
        return http.HttpResponseBadRequest('Missing s3_object_type')

    now = datetime.datetime.utcnow()
    name, ext = os.path.splitext(object_name)
    name = slugify(name)
    name = hashlib.md5(name).hexdigest()[:5]
    name = '%s-%s-%s' % (request.user.id, name, now.strftime('%H%M%S'))
    ext = ext.lower()
    directory = now.strftime('%Y/%m/%d')
    object_name = os.path.join(directory, '%s%s' % (name, ext))

    expires = int(time.time() + 10)
    amz_headers = "x-amz-acl:public-read"

    put_request = (
        "PUT\n\n%s\n%d\n%s\n/%s/%s" % (
            mime_type,
            expires,
            amz_headers,
            S3_UPLOAD_BUCKET,
            object_name
        )
    )

    signature = base64.encodestring(
        hmac.new(AWS_SECRET_KEY, put_request, hashlib.sha1).digest()
    )
    signature = urllib.quote_plus(signature.strip())

    url = 'https://%s.s3.amazonaws.com/%s' % (S3_UPLOAD_BUCKET, object_name)
    context = {}
    context['url'] = url
    signed_request = (
        '%s?AWSAccessKeyId=%s&Expires=%d&Signature=%s' % (
            url,
            AWS_ACCESS_KEY,
            expires,
            signature
        )
    )
    cache_key = 'file_name_%s' % hashlib.md5(url).hexdigest()
    cache.set(cache_key, file_name, 60 * 60)
    cache_key = 'mime_type_%s' % hashlib.md5(url).hexdigest()
    cache.set(cache_key, mime_type, 60 * 60)

    context['signed_request'] = signed_request
    return context


@json_view
@login_required
@require_POST
def save(request):
    url = request.POST.get('url')
    if not url:
        return http.HttpResponseBadRequest('Not a valid URL')
    cache_key = 'length_%s' % hashlib.md5(url).hexdigest()
    size = cache.get(cache_key)
    if not size:
        r = requests.head(url)
        size = int(r.headers['content-length'])
        if not size:
            return http.HttpResponseBadRequest('URL could not be downloaded')
    cache_key = 'file_name_%s' % hashlib.md5(url).hexdigest()
    file_name = cache.get(cache_key)
    if not file_name:
        file_name = os.path.basename(url)

    cache_key = 'mime_type_%s' % hashlib.md5(url).hexdigest()
    mime_type = cache.get(cache_key)

    new_upload = Upload.objects.create(
        user=request.user,
        url=url,
        size=size,
        file_name=file_name,
        mime_type=mime_type
    )
    messages.info(
        request,
        'Upload saved.'
    )
    context = {'id': new_upload.pk, 'url': new_upload.url}
    if request.session.get('active_event'):
        event_id = request.session['active_event']
        event = Event.objects.get(pk=event_id)
        event.upload = new_upload
        event.save()
        new_upload.event = event
        new_upload.save()
        next_url = reverse('manage:event_archive', args=(event_id,))
        next_url += '#vidly-shortcutter'
        context['event'] = {
            'url': next_url,
            'title': event.title,
        }
    elif request.session.get('active_suggested_event'):
        event_id = request.session['active_suggested_event']
        event = SuggestedEvent.objects.get(pk=event_id)
        event.upload = new_upload
        event.save()
        new_upload.suggested_event = event
        new_upload.save()
        next_url = reverse('suggest:description', args=(event_id,))
        context['suggested_event'] = {
            'url': next_url,
            'title': event.title
        }
    return context


@json_view
def verify_size(request):
    url = request.GET.get('url')
    r = requests.head(url)
    size = int(r.headers['content-length'])
    cache_key = 'length_%s' % hashlib.md5(url).hexdigest()
    cache.set(cache_key, size, 60 * 60)
    context = {
        'size': size,
        'size_human': filesizeformat(size)
    }
    return context
