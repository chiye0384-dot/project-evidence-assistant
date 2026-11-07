"""Explicit process-local loopback proxy only; no environment or system changes."""
def checked_proxy_port(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError('Invalid local proxy port; no request sent')
    return value

def client_options(proxy_port=0):
    port=checked_proxy_port(proxy_port)
    return {'proxy':'http://127.0.0.1:'+str(port)} if port else {}

def public_route(proxy_port=0):
    port=checked_proxy_port(proxy_port)
    return dict(mode='loopback_http_proxy',port=port) if port else None
