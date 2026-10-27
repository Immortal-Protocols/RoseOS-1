package dev.pixel.voice;

import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.security.keystore.*;
import java.net.*;
import java.io.*;
import java.security.*;
import java.security.spec.ECGenParameterSpec;
import java.security.cert.X509Certificate;
import java.util.*;
import javax.net.ssl.*;
import javax.security.auth.x500.X500Principal;
import org.json.JSONObject;

/** Device-only TLS identity. The APK contains public server pins, never a secret. */
final class DeviceIdentity {
 static final String ALIAS="pixel-mac-pairing-v2";
 private final Context context;
 private final KeyStore store;
 DeviceIdentity(Context context)throws Exception{
  this.context=context.getApplicationContext();store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
  if(!store.containsAlias(ALIAS)){
   boolean strong=context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE);
   try{generate(strong);}catch(StrongBoxUnavailableException e){generate(false);}
  }
  // A software key silently undermines the promise made in the pairing UI.
  if(!hardwareBacked())throw new GeneralSecurityException("Hardware-backed key storage unavailable");
 }
 private void generate(boolean strong)throws Exception{
  KeyPairGenerator gen=KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC,"AndroidKeyStore");
  KeyGenParameterSpec.Builder b=new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_SIGN|KeyProperties.PURPOSE_VERIFY)
   .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
   // Android's TLS stack hashes handshake messages itself and requests a raw
   // ECDSA signature. SHA256-only keys work in ordinary Signature tests but
   // fail TLS before HTTP reaches the Mac. See KeyGenParameterSpec.setDigests.
   .setDigests(KeyProperties.DIGEST_NONE,KeyProperties.DIGEST_SHA256)
   .setCertificateSubject(new X500Principal("CN=Rose Agent Device"))
   .setCertificateSerialNumber(new java.math.BigInteger(120,new SecureRandom()))
   .setCertificateNotBefore(new Date(System.currentTimeMillis()-86400000L))
   .setCertificateNotAfter(new Date(System.currentTimeMillis()+20L*365*86400000L));
  if(strong)b.setIsStrongBoxBacked(true);
  gen.initialize(b.build());gen.generateKeyPair();
 }
 private PrivateKey privateKey()throws Exception{return (PrivateKey)store.getKey(ALIAS,null);}
 boolean hardwareBacked()throws Exception{
  KeyInfo info=KeyFactory.getInstance("EC","AndroidKeyStore").getKeySpec(privateKey(),KeyInfo.class);
  return info.isInsideSecureHardware()&&privateKey().getEncoded()==null;
 }
 String id()throws Exception{return VoiceService.hex(MessageDigest.getInstance("SHA-256").digest(store.getCertificate(ALIAS).getPublicKey().getEncoded()));}
 private X509KeyManager keyManager(){
  X509KeyManager manager=new X509ExtendedKeyManager(){
   private String choose(String[] types){for(String type:types)if(type.equals("EC"))return ALIAS;return null;}
   public String chooseClientAlias(String[] types,Principal[] issuers,Socket socket){return choose(types);}
   public String chooseEngineClientAlias(String[] types,Principal[] issuers,SSLEngine engine){return choose(types);}
   public String[] getClientAliases(String type,Principal[] issuers){return type.equals("EC")?new String[]{ALIAS}:null;}
   public X509Certificate[] getCertificateChain(String alias){try{return ALIAS.equals(alias)?new X509Certificate[]{(X509Certificate)store.getCertificate(ALIAS)}:null;}catch(Exception e){return null;}}
   public PrivateKey getPrivateKey(String alias){try{return ALIAS.equals(alias)?privateKey():null;}catch(Exception e){return null;}}
   public String chooseServerAlias(String type,Principal[] issuers,Socket socket){return null;}
   public String[] getServerAliases(String type,Principal[] issuers){return null;}
  };
  return manager;
 }
 SSLSocketFactory systemTrustedSsl()throws Exception{
  TrustManagerFactory factory=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());factory.init((KeyStore)null);
  SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(new KeyManager[]{keyManager()},factory.getTrustManagers(),new SecureRandom());return ssl.getSocketFactory();
 }
 SSLSocketFactory ssl(String pin)throws Exception{
  if(pin==null||!pin.matches("[a-f0-9]{64}"))throw new GeneralSecurityException("Missing server certificate pin");
  X509TrustManager trust=new X509TrustManager(){
   public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
   public void checkClientTrusted(X509Certificate[] chain,String auth)throws java.security.cert.CertificateException{throw new java.security.cert.CertificateException("Not a server");}
   public void checkServerTrusted(X509Certificate[] chain,String auth)throws java.security.cert.CertificateException{
    try{if(chain==null||chain.length==0||!pin.equals(VoiceService.hex(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()))))throw new GeneralSecurityException("Server identity mismatch");chain[0].checkValidity();}
    catch(Exception e){throw new java.security.cert.CertificateException(e);}
   }
  };
  SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(new KeyManager[]{keyManager()},new TrustManager[]{trust},new SecureRandom());return ssl.getSocketFactory();
 }
 JSONObject pair(String code)throws Exception{
  JSONObject config=new JSONObject(VoiceService.read(context.getAssets().open("connection.json")));
  String endpoint=context.getSharedPreferences("MainActivity",0).getString("endpoint",config.getString("endpoint"));
  HttpsURLConnection c=(HttpsURLConnection)new URL(endpoint+"/pair/enroll").openConnection();
  c.setSSLSocketFactory(ssl(config.getString("pin")));c.setHostnameVerifier((h,s)->true);
  c.setConnectTimeout(6000);c.setReadTimeout(12000);c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");
  try{
   byte[] body=new JSONObject().put("code",code).put("label",Build.MODEL).toString().getBytes("UTF-8");
   try(OutputStream out=c.getOutputStream()){out.write(body);}
   int status=c.getResponseCode();JSONObject result=new JSONObject(VoiceService.read(status<400?c.getInputStream():c.getErrorStream()));
   if(status!=200||!result.optBoolean("paired")||!id().equals(result.optString("deviceId")))throw new IOException(result.optString("error","Pairing could not be verified"));
   // Do not remember success until a fresh protected request proves the server
   // accepted this identity, independently of the enrollment response.
   HttpsURLConnection check=(HttpsURLConnection)new URL(endpoint+"/pair/status").openConnection();
   check.setSSLSocketFactory(ssl(config.getString("pin")));check.setHostnameVerifier((h,s)->true);check.setConnectTimeout(6000);check.setReadTimeout(12000);
   try{if(check.getResponseCode()!=200)throw new IOException("Paired connection verification failed");JSONObject confirmed=new JSONObject(VoiceService.read(check.getInputStream()));if(!id().equals(confirmed.optString("deviceId")))throw new IOException("Device identity mismatch");}finally{check.disconnect();}
   context.getSharedPreferences("device-pairing",0).edit().putBoolean("paired",true).commit();
   return result;
  }finally{c.disconnect();}
 }
 static boolean paired(Context c){return c.getSharedPreferences("device-pairing",0).getBoolean("paired",false);}
}
