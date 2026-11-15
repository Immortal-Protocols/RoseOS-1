package dev.pixel.rose.authenticator;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Seals the authenticator's secrets with one non-exportable AES-256-GCM key in Android Keystore (hardware-backed
 * where the device has it). The key works only while the device is unlocked, so a locked phone shows no codes.
 * Output: 12-byte IV, then ciphertext and 128-bit tag; the entry's (issuer, account) is associated data.
 */
final class KeystoreSealer implements Vault.Sealer {
    static final String ALIAS = "rose-authenticator-v1";
    private static final String STORE = "AndroidKeyStore";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";

    private SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance(STORE);
        ks.load(null);
        if (ks.containsAlias(ALIAS)) return (SecretKey) ks.getKey(ALIAS, null);
        KeyGenerator gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, STORE);
        gen.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .setUnlockedDeviceRequired(true)
                .build());
        return gen.generateKey();
    }

    @Override public byte[] seal(byte[] plaintext, byte[] associatedData) throws Exception {
        Cipher c = Cipher.getInstance(TRANSFORMATION);
        c.init(Cipher.ENCRYPT_MODE, key());
        c.updateAAD(associatedData);
        byte[] ct = c.doFinal(plaintext);
        byte[] iv = c.getIV();
        byte[] out = Arrays.copyOf(iv, iv.length + ct.length);
        System.arraycopy(ct, 0, out, iv.length, ct.length);
        return out;
    }

    @Override public byte[] open(byte[] sealed, byte[] associatedData) throws Exception {
        if (sealed.length < 12 + 16) throw new IllegalArgumentException("sealed secret too short");
        Cipher c = Cipher.getInstance(TRANSFORMATION);
        c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, sealed, 0, 12));
        c.updateAAD(associatedData);
        return c.doFinal(sealed, 12, sealed.length - 12);
    }
}
