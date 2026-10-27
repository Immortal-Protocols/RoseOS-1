package dev.pixel.voice;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Context;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.hardware.display.DisplayManager;
import android.os.Build;
import android.os.CancellationSignal;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyPermanentlyInvalidatedException;
import android.security.keystore.KeyProperties;
import android.security.keystore.UserNotAuthenticatedException;
import android.view.Display;
import android.widget.Toast;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Owner-armed background app control while the phone is locked or its screen is off.
 * Arming needs the owner's PIN, pattern, password or strong biometric. Each locked request then
 * uses a Keystore key that Android only releases within AUTH_WINDOW_SECONDS of such an
 * authentication, including an ordinary unlock. The keyguard itself is never dismissed, and the
 * physical screen is never driven while locked: control runs only in the background workspace.
 */
final class LockedControl {
 static final String PREFS="locked-control",ENABLED="enabled",KEY="pixel-voice-locked-control-v1";
 // Matches config_pixelComputerControlLockedAuthMaxAgeMs in locked-control-frameworks-base.patch.
 static final int AUTH_WINDOW_SECONDS=4*60*60;
 static final int AUTHENTICATORS=BiometricManager.Authenticators.BIOMETRIC_STRONG|BiometricManager.Authenticators.DEVICE_CREDENTIAL;
 // Operations that act in, or create, a background session. State, stop and app lists stay open.
 static boolean gates(String operation){return "start".equals(operation)||"command".equals(operation);}
 static boolean enabled(Context c){return c.getSharedPreferences(PREFS,0).getBoolean(ENABLED,false);}
 static boolean ownerAway(Context c){
  // The physical display's own state: a running background display keeps the device interactive.
  Display phone=((DisplayManager)c.getSystemService(Context.DISPLAY_SERVICE)).getDisplay(Display.DEFAULT_DISPLAY);
  return ((KeyguardManager)c.getSystemService(Context.KEYGUARD_SERVICE)).isDeviceLocked()||phone==null||phone.getState()!=Display.STATE_ON;
 }
 /** Null when an owner-armed request may proceed while the owner is away; otherwise the refusal. */
 static JSONObject refusal(Context c)throws JSONException{
  if(!enabled(c))return locked("not_enabled","App control while locked is off. Unlock the phone, or turn it on in Rose Agent options.");
  return keyRefusal(c);
 }
 static JSONObject keyRefusal(Context c)throws JSONException{
  if(Build.VERSION.SDK_INT<30)return locked("unsupported","App control while locked needs Android 11 or newer.");
  if(!((KeyguardManager)c.getSystemService(Context.KEYGUARD_SERVICE)).isDeviceSecure()){disable(c);return locked("no_secure_lock","App control while locked needs a PIN, pattern or password.");}
  try{
   KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
   PrivateKey key=(PrivateKey)store.getKey(KEY,null);
   if(key==null){disable(c);return locked("not_enabled","App control while locked is off. Turn it on again in Rose Agent options.");}
   byte[] nonce=new byte[32];new SecureRandom().nextBytes(nonce);
   Signature signature=Signature.getInstance("SHA256withECDSA");signature.initSign(key);signature.update(nonce);signature.sign();
   return null;
  }catch(UserNotAuthenticatedException expired){return locked("authentication_expired","Unlock the phone with your PIN or strong biometric to renew app control while locked.");}
  catch(KeyPermanentlyInvalidatedException invalidated){disable(c);return locked("invalidated","Screen lock or biometrics changed. Turn on app control while locked again.");}
  catch(Exception e){return locked("unavailable","Locked control authorization is unavailable: "+e.getClass().getSimpleName());}
 }
 static JSONObject locked(String state,String message)throws JSONException{
  return new JSONObject().put("status","locked").put("reason","owner_unlock_required").put("lockedControl",state).put("message",message).put("actionExecuted",false).put("foregroundFallback",false);
 }
 static void disable(Context c){
  c.getSharedPreferences(PREFS,0).edit().putBoolean(ENABLED,false).commit();
  try{KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);store.deleteEntry(KEY);}catch(Exception ignored){}
 }
 static void createKey()throws Exception{
  KeyPairGenerator generator=KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC,"AndroidKeyStore");
  generator.initialize(new KeyGenParameterSpec.Builder(KEY,KeyProperties.PURPOSE_SIGN).setDigests(KeyProperties.DIGEST_SHA256)
   .setUserAuthenticationRequired(true).setUserAuthenticationParameters(AUTH_WINDOW_SECONDS,KeyProperties.AUTH_DEVICE_CREDENTIAL|KeyProperties.AUTH_BIOMETRIC_STRONG)
   .setInvalidatedByBiometricEnrollment(true).build());
  generator.generateKeyPair();
 }
 /** Arms locked control after the owner confirms with Android's own credential prompt. */
 static void enable(Activity activity,Runnable done){
  if(Build.VERSION.SDK_INT<30){Toast.makeText(activity,"App control while locked needs Android 11 or newer.",1).show();return;}
  if(!((KeyguardManager)activity.getSystemService(Context.KEYGUARD_SERVICE)).isDeviceSecure()){Toast.makeText(activity,"Set a PIN, pattern or password first.",1).show();return;}
  try{disable(activity);createKey();}catch(Exception e){disable(activity);Toast.makeText(activity,"Could not create the authorization key: "+e.getClass().getSimpleName(),1).show();return;}
  BiometricPrompt prompt=new BiometricPrompt.Builder(activity).setTitle("Allow app control while locked").setSubtitle("Rose Agent")
   .setDescription("For up to 4 hours after you unlock with your PIN or strong biometric, your assistant can keep working in apps in its background workspace while the screen is off or locked. The lock screen stays on. Installs, permissions, settings and secure screens still wait for you.")
   .setAllowedAuthenticators(AUTHENTICATORS).build();
  prompt.authenticate(new CancellationSignal(),activity.getMainExecutor(),new BiometricPrompt.AuthenticationCallback(){
   @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult result){
    JSONObject refusal;try{refusal=keyRefusal(activity);}catch(JSONException impossible){refusal=new JSONObject();}
    if(refusal==null){activity.getSharedPreferences(PREFS,0).edit().putBoolean(ENABLED,true).commit();Toast.makeText(activity,"App control while locked is on.",0).show();}
    else{disable(activity);Toast.makeText(activity,"Could not verify the authorization key. App control while locked stays off.",1).show();}
    done.run();
   }
   @Override public void onAuthenticationError(int code,CharSequence message){disable(activity);Toast.makeText(activity,String.valueOf(message),0).show();done.run();}
  });
 }
}
