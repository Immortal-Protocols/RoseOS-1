package dev.pixel.voice;

import android.app.KeyguardManager;
import android.content.Context;
import android.hardware.display.DisplayManager;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyPermanentlyInvalidatedException;
import android.security.keystore.KeyProperties;
import android.security.keystore.UserNotAuthenticatedException;
import android.view.Display;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Background app control while the phone is locked or its screen is off. It is always on: there is no
 * setting and nothing to arm (owner decision, 2026-11-07). The owner's protection is Android's own:
 * each locked request signs with a Keystore key that Android only releases within AUTH_WINDOW_SECONDS
 * of a PIN, pattern, password or strong biometric authentication, including an ordinary unlock, and
 * the OS separately refuses locked sessions unless its locked-session policy is on and its own
 * owner-authentication checks pass. The key is created on first use; creating it needs no
 * authentication, prompt or permission, and it signs nothing until the owner has authenticated.
 * The keyguard itself is never dismissed, and the physical screen is never driven while locked:
 * control runs only in the background workspace.
 */
final class LockedControl {
 static final String KEY="pixel-voice-locked-control-v1";
 // Matches config_pixelComputerControlLockedAuthMaxAgeMs in locked-control-frameworks-base.patch.
 static final int AUTH_WINDOW_SECONDS=4*60*60;
 // Operations that act in, or create, a background session. State, stop and app lists stay open.
 static boolean gates(String operation){return "start".equals(operation)||"command".equals(operation);}
 static boolean ownerAway(Context c){
  // The physical display's own state: a running background display keeps the device interactive.
  Display phone=((DisplayManager)c.getSystemService(Context.DISPLAY_SERVICE)).getDisplay(Display.DEFAULT_DISPLAY);
  return ((KeyguardManager)c.getSystemService(Context.KEYGUARD_SERVICE)).isDeviceLocked()||phone==null||phone.getState()!=Display.STATE_ON;
 }
 /** Null when a request may proceed while the owner is away; otherwise the refusal. */
 static JSONObject refusal(Context c)throws JSONException{
  if(Build.VERSION.SDK_INT<30)return locked("unsupported","App control while locked needs Android 11 or newer.");
  if(!((KeyguardManager)c.getSystemService(Context.KEYGUARD_SERVICE)).isDeviceSecure())return locked("no_secure_lock","App control while locked needs a PIN, pattern or password.");
  try{
   KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
   try{sign(key(store));}
   catch(KeyPermanentlyInvalidatedException invalidated){
    // Screen lock or biometric enrollment changed: replace the key. It too signs only after a fresh owner authentication.
    store.deleteEntry(KEY);sign(key(store));
   }
   return null;
  }catch(UserNotAuthenticatedException expired){return locked("authentication_expired","Unlock the phone with your PIN or strong biometric to renew app control while locked.");}
  catch(Exception e){return locked("unavailable","Locked control authorization is unavailable: "+e.getClass().getSimpleName());}
 }
 /** This install's key, created on first use. */
 static PrivateKey key(KeyStore store)throws Exception{
  PrivateKey key=(PrivateKey)store.getKey(KEY,null);
  if(key!=null)return key;
  createKey();
  return (PrivateKey)store.getKey(KEY,null);
 }
 static void sign(PrivateKey key)throws Exception{
  byte[] nonce=new byte[32];new SecureRandom().nextBytes(nonce);
  Signature signature=Signature.getInstance("SHA256withECDSA");signature.initSign(key);signature.update(nonce);signature.sign();
 }
 static JSONObject locked(String state,String message)throws JSONException{
  return new JSONObject().put("status","locked").put("reason","owner_unlock_required").put("lockedControl",state).put("message",message).put("actionExecuted",false).put("foregroundFallback",false);
 }
 static void createKey()throws Exception{
  KeyPairGenerator generator=KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC,"AndroidKeyStore");
  generator.initialize(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_SIGN).setDigests(KeyProperties.DIGEST_SHA256)
   .setUserAuthenticationRequired(true).setUserAuthenticationParameters(AUTH_WINDOW_SECONDS,KeyProperties.AUTH_DEVICE_CREDENTIAL|KeyProperties.AUTH_BIOMETRIC_STRONG)
   .setInvalidatedByBiometricEnrollment(true).build());
  generator.generateKeyPair();
 }
}
