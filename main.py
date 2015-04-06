import os
import sys
import traceback
import time
import datetime
import base64
import tornado.httpserver
import tornado.ioloop
import tornado.web
from tornado.websocket import WebSocketHandler
from tornado.web import asynchronous
import json
import random
import uuid


# Global ID seed for clients
clients_connected = 0
proxies = []
clients = {}

class ProxyListHttpHandler(tornado.web.RequestHandler):
    def get(self):
        global proxies
        concerts = []
        for proxy in proxies:
            pry = {}
            pry['name'] = proxy.name
            pry['user_auth'] = proxy.user_auth
            concerts.append(pry)
        answer = {}
        answer['concerts'] = concerts
        message = json.dumps(answer)
        self.set_status(200)
        self.write(message)


class VideoHttpHandler(tornado.web.RequestHandler):
    @asynchronous
    def get(self):
        global proxies, clients

        args = {}
        args['topic'] = self.get_argument('topic')
        args['height'] = self.get_argument('height','480')
        args['width'] = self.get_argument('width','640')

        session_id = self.get_cookie('session_id')
        if session_id != None:
            self.set_status(200)
            self.set_header('server', 'example')
            self.set_header('connection', 'close')
            self.set_header('pragma', 'no-cache')
            self.set_header('cache-control', 'no-cache, no-store, must-revalidate,'
                        'pre-check=0, post-check=0, max-age=0')
            self.set_header('access-control-allow-origin', '*')
            self.set_header('content-type', 'multipart/x-mixed-replace;boundary='
                        '--boundarydonotcross')

            client = clients.get(session_id)
            if client is not None and client.proxy is not None:
                if client.authenticated or not proxy.user_auth:
                    message = json.dumps({"op":"videoStart", "url_params" : args, "session_id": session_id})
                    if client.video_conn is None:
                        client.proxy.conn.write_message(message)
                    client.video_conn = self
                    return
        #TODO Include a better error response
        self.set_status(401)
        self.finish()


class RosbridgeProxyHandler(WebSocketHandler):
    def __init__(self, application, request, **kwargs):
        tornado.websocket.WebSocketHandler.__init__(self, application, request, **kwargs)
        self.io_loop = tornado.ioloop.IOLoop.instance()
        self.ping_interval = int(os.environ.get("PING_INTERVAL", 5))

    def open(self):
        global clients_connected, authenticate, clients
        clients_connected += 1
        print "Client connected.  %d clients total." % clients_connected
        self.io_loop.add_timeout(datetime.timedelta(seconds=self.ping_interval), self.send_ping)

    def send_ping(self):
        try:
            self.ping("a")
        except Exception as ex:
            print "-- Failed to send ping! %s" % ex

    def on_pong(self, data):
        self.io_loop.add_timeout(datetime.timedelta(seconds=self.ping_interval), self.send_ping)


    def on_message(self, message):
        global proxies, clients
        print message
        try:
            msg = json.loads(message)
            if msg['op'] == 'proxy':
                self.add_proxy(msg,proxies)
            elif msg['op'] == 'auth_client':
                session_id = msg['session_id']
                auth = msg['authentication']
                client = clients[session_id]
                client.authenticated = auth
                print "Client ", session_id ," authenticated ", auth
                #TODO SEND AUTH MSG TO CLIENT
                clientMsg = {}
                clientMsg['op'] = 'service_response'
                clientMsg['id'] = 'login'
                clientMsg['login_result'] = auth
                client.ws_conns[-1].write_message(json.dumps(clientMsg))
                if not auth:
                    client.ws_conns[-1].close()
            elif msg.get('session_id') != None:
                #It's a proxy to client msg
                if msg['op'] == 'videoData':
                    self.send_video(msg,clients)
                elif msg['op'] == 'endVideo':
                    self.end_video(clients,msg)
                else:
                    self.pass_message(msg,clients)
            else:
                #It's not a proxy, check for auth
                session_id = self.get_cookie('session_id')
                client = clients.get(session_id)
                if client == None:
                    client = Client(session_id)
                    clients[session_id] = client
                client.ws_conns.append(self)
                if msg['op'] == 'auth': #In the authentication is included the proxy id
                    msg['session_id'] = session_id
                    print msg
                    message = json.dumps(msg)
                    for proxy in proxies:
                        if proxy.name == msg['proxy_name']:
                            client.proxy = proxy
                            print "Client", session_id," bound to proxy ", proxy.name
                            break
                    client.proxy.conn.write_message(message)
                elif client.authenticated or (client.proxy != None and not client.proxy.user_auth):
                    self.pass_message(msg,clients)
                else:
                    print "Client not authenticated"
                    #self.close()
                    #client.ws_conns.remove(self)
        except Exception as e:
            print "Unexpected error:", sys.exc_info()[0]
            traceback.print_exc()

    def add_proxy(self,msg,proxies):
        user_auth = msg['user_auth']
        proxy_name = msg['name']
        proxy = Proxy(self,proxy_name,user_auth)

        proxies.append(proxy)
        print "It's a proxy!"
        print "Proxy ID = ", proxy.name

    def send_video(self, msg, clients):
        try:
            session_id = msg["session_id"]
            client = clients.get(session_id)
            if client != None and client.video_conn != None:
                if not client.video_conn.request.connection.stream.closed():
                    decoded = base64.b64decode(msg['data'])
                    client.video_conn.write(decoded)
                    client.video_conn.flush()
                else:
                    print "Navigator closed"
                    client.proxy.conn.write_message(json.dumps({"op":"endVideo","session_id":session_id}))
                    client.video_conn = None
                    self.remove_client(clients, client)
        except Exception as e:
            print e

    def end_video(self, clients, msg):
        session_id = msg["session_id"]
        client = clients.get(session_id)
        if client != None:
            client.video_conn.finish()
            client.video_conn = None
            self.remove_client(clients,client)

    def pass_message(self, msg, clients):
        session_id = self.get_cookie('session_id')
        if session_id != None:
            client = clients.get(session_id)
            if client != None and client.proxy != None:
                msg['session_id'] = client.session_id
                message = json.dumps(msg)
                client.proxy.conn.write_message(message)
        else:
            dest = msg.get('session_id')
            message = json.dumps(msg)
            if dest != None:
                client = clients.get(dest)
                if client != None and client.ws_conns[-1]!= None:
                    client.ws_conns[-1].write_message(message)
            else:
                #TODO IF NO DEST, SEND TO ALL
                for client in clients.itervalues():
                    if client.ws_conns[-1] != None:
                        client.ws_conns[-1].write_message(message)



    def on_close(self):
        global clients_connected, proxies, clients
        clients_connected = clients_connected - 1
        print "Client disconnected. %d clients total." % clients_connected
        session_id = self.get_cookie('session_id')
        if session_id != None:
            client = clients.get(session_id)
            if client != None:
               # client.ws_conn = None
               # self.remove_client(clients,client)
               pass
        else:
            for proxy in proxies:
                if proxy.conn == self:
                    proxies.remove(proxy)
                    print "proxy removed"
                    break

    def remove_client(self,clients,client):
        if client.ws_conn == None:
            msg = json.dumps({"op":"endConn","session_id" : client.session_id})
            client.proxy.conn.write_message(msg)
            if client.video_conn == None:
                del clients[client.session_id]
                print "Client Removed"

    def check_origin(self, origin):
        return True

class Proxy():
    def __init__(self,proxy_conn,proxy_name,user_auth=False):
        self.conn = proxy_conn
        self.name = proxy_name
        self.user_auth = user_auth

class Client():
    def __init__(self,session_id,proxy=None,ws_conn=None,video_conn=None):
        self.proxy = proxy
        self.authenticated = False
        self.ws_conns = []
        self.ws_conns.append(ws_conn)
        self.video_conn = video_conn
        self.session_id = session_id

class MyFileHandler(tornado.web.StaticFileHandler):
    def set_headers(self):
        global clients
        cookie = self.get_cookie('session_id')
        if cookie == None:
            print "No cookie, client created"
            self.set_cookie('session_id',str(uuid.uuid1()))
        super(MyFileHandler,self).set_headers()


def main():
    application = tornado.web.Application([
        (r"/proxy_list", ProxyListHttpHandler),    
        (r"/stream", VideoHttpHandler),
        (r"/ws", RosbridgeProxyHandler),
        (r"/(.*)", MyFileHandler, {"path": "./www"}),
    ])
    http_server = tornado.httpserver.HTTPServer(application)
    port = int(os.environ.get("PORT", 9090))
    http_server.listen(port)

    print "ROCON Web Proxy Server started on port %d" % port

    tornado.ioloop.IOLoop.instance().start()

if __name__ == "__main__":
    main()


