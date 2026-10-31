package dev.pixel.voice;

import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyInfo;
import android.security.keystore.KeyProperties;
import android.security.keystore.StrongBoxUnavailableException;

import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.cert.Certificate;
import java.security.spec.ECGenParameterSpec;

/**
 * This phone's identity for Rose on the Mac: an EC P-256 signing key in the Android Keystore
 * (StrongBox when the phone has one, otherwise the TEE). The private key never leaves secure
 * hardware; the Mac stores only the public key it was given at pairing. Created at the first
 * pairing and reused when pairing again. A key outside secure hardware is refused.
 */
final class LocalRoseKey implements LocalRoseAuth.Signer {
    static final String ALIAS = "rose-local-device-v1";

    private final PrivateKey key;
    private final byte[] publicKey;  // SPKI DER

    private LocalRoseKey(KeyStore store) throws GeneralSecurityException {
        this.key = (PrivateKey) store.getKey(ALIAS, null);
        Certificate cert = store.getCertificate(ALIAS);
        if (key == null || cert == null) throw new GeneralSecurityException("Rose pairing key is missing");
        this.publicKey = cert.getPublicKey().getEncoded();
    }

    /** The existing key, or null before the first pairing. */
    static LocalRoseKey load() throws GeneralSecurityException {
        KeyStore store = keyStore();
        if (!store.containsAlias(ALIAS)) return null;
        LocalRoseKey k = new LocalRoseKey(store);
        k.requireSecureHardware();
        return k;
    }

    /** The existing key, or a new one in secure hardware. */
    static synchronized LocalRoseKey loadOrCreate(Context context) throws GeneralSecurityException {
        KeyStore store = keyStore();
        if (!store.containsAlias(ALIAS)) {
            boolean strongBox = context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE);
            try {
                generate(strongBox);
            } catch (StrongBoxUnavailableException e) {
                generate(false);
            }
        }
        LocalRoseKey k = new LocalRoseKey(store);
        try {
            k.requireSecureHardware();
        } catch (GeneralSecurityException e) {
            store.deleteEntry(ALIAS);  // never keep (or later use) a software key
            throw e;
        }
        return k;
    }

    private static void generate(boolean strongBox) throws GeneralSecurityException {
        KeyPairGenerator gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
        KeyGenParameterSpec.Builder b = new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256);
        if (strongBox) b.setIsStrongBoxBacked(true);
        gen.initialize(b.build());
        gen.generateKeyPair();
    }

    private void requireSecureHardware() throws GeneralSecurityException {
        KeyInfo info = KeyFactory.getInstance(key.getAlgorithm(), "AndroidKeyStore").getKeySpec(key, KeyInfo.class);
        boolean secure;
        if (Build.VERSION.SDK_INT >= 31) {
            int level = info.getSecurityLevel();
            secure = level == KeyProperties.SECURITY_LEVEL_STRONGBOX || level == KeyProperties.SECURITY_LEVEL_TRUSTED_ENVIRONMENT
                    || level == KeyProperties.SECURITY_LEVEL_UNKNOWN_SECURE;
        } else {
            secure = info.isInsideSecureHardware();
        }
        if (!secure || key.getEncoded() != null) {
            throw new GeneralSecurityException("This phone can't keep Rose's pairing key in secure hardware, so it can't pair.");
        }
    }

    private static KeyStore keyStore() throws GeneralSecurityException {
        try {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore");
            store.load(null);
            return store;
        } catch (java.io.IOException e) {
            throw new GeneralSecurityException(e);
        }
    }

    /** SubjectPublicKeyInfo DER, as sent to the Mac in POST /pair. */
    byte[] publicKey() {
        return publicKey.clone();
    }

    String deviceId() {
        return LocalRoseAuth.deviceId(publicKey);
    }

    @Override
    public byte[] sign(byte[] message) throws GeneralSecurityException {
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign(key);
        s.update(message);
        return s.sign();
    }
}
