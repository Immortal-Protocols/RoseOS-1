#!/usr/bin/env python3
import urllib.request,urllib.error,xml.etree.ElementTree as ET,json,sys
URL='http://192.168.4.1:1900/ipc'
SERVICE='urn:schemas-upnp-org:service:WANIPConnection:1'
def call(action,**params):
 from xml.sax.saxutils import escape
 args=''.join('<'+k+'>'+escape(str(v))+'</'+k+'>' for k,v in params.items())
 body=('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:'+action+' xmlns:u="'+SERVICE+'">'+args+'</u:'+action+'></s:Body></s:Envelope>').encode()
 req=urllib.request.Request(URL,data=body,headers={'Content-Type':'text/xml; charset="utf-8"','SOAPAction':'"'+SERVICE+'#'+action+'"'})
 try:r=urllib.request.urlopen(req,timeout=5);raw=r.read()
 except urllib.error.HTTPError as e:raw=e.read()
 tree=ET.fromstring(raw);return {x.tag.split('}')[-1]:x.text for x in tree.iter() if len(x)==0}
if __name__=='__main__':
 print(json.dumps(call('GetExternalIPAddress')))
 print(json.dumps(call('GetSpecificPortMappingEntry',NewRemoteHost='',NewExternalPort=49443,NewProtocol='TCP')))
