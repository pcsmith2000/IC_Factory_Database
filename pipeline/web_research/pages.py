"""Page-fetching helpers shared by research: an SSRF-safe opener and public-contact decoders.

Written for the September 2026 Tako pass (since retired); research_eval's Fetcher and coordinate
recovery still use them.
"""
from __future__ import annotations
import ipaddress
import re
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler
from bs4 import BeautifulSoup


def norm(v):
    return re.sub(r'[^a-z0-9]', '', str(v or '').lower())

def domain(url):
    if url and '://' not in url: url='https://'+url
    try:
        return (urlparse(url).hostname or '').lower().removeprefix('www.')
    except (ValueError, TypeError):
        return ''

def safe_url(url):
    p = urlparse(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.port not in (None,80,443):
        raise ValueError('Not a public HTTP URL')
    addresses = socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Non-public destination')
    return url

class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def decode_contact_spans(soup):
    """Decode the public EEB contact-span format, without executing JavaScript."""
    from urllib.parse import unquote
    for script in soup.find_all('script'):
        code=script.string or ''
        values=re.search(r'var ml="([^"\\]*)",mi="([^"\\]*)"',code)
        target=re.search(r'getElementById\("([^"\\]*)"\)',code)
        if not values or not target: continue
        alphabet,encoded=values.groups()
        if len(encoded)>5000 or any(not 0<=ord(c)-48<len(alphabet) for c in encoded): continue
        span=soup.find(id=target.group(1))
        if span is not None:
            decoded=unquote(''.join(alphabet[ord(c)-48] for c in encoded))
            span.append(BeautifulSoup(decoded,'html.parser'))

def decode_public_email_links(soup):
    """Expose publicly encoded contact addresses as text; never infer or execute code."""
    from urllib.parse import unquote
    email_pattern=r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+'
    for node in soup.select('[data-cfemail]'):
        try:
            encoded=bytes.fromhex(node.get('data-cfemail',''))
            if not 2<=len(encoded)<=256:continue
            email=bytes(b^encoded[0] for b in encoded[1:]).decode('utf-8')
            if re.fullmatch(email_pattern,email):node.clear();node.append(email)
        except (ValueError,UnicodeError):continue
    for node in soup.find_all('a',href=True):
        href=node['href']
        if not href.lower().startswith('mailto:'):continue
        email=unquote(href[7:].split('?',1)[0])
        if re.fullmatch(email_pattern,email) and email not in node.get_text():
            node.append(' '+email)
