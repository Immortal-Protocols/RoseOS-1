package dev.pixel.models;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.cert.Certificate;
import java.security.spec.ECGenParameterSpec;
import java.util.ArrayList;
import java.util.List;

/** service-auth-v1 device key: a non-exportable, hardware-attested EC P-256 key in AndroidKeyStore. */
final class KeystoreDeviceKey implements DeviceKey {
    private static final String STORE = "AndroidKeyStore";
    private static final String ALIAS = "rose-models-device";

    private static KeyStore keyStore() throws Exception {
        KeyStore ks = KeyStore.getInstance(STORE);
        ks.load(null);
        return ks;
    }

    @Override
    public boolean exists() {
        try {
            return keyStore().containsAlias(ALIAS);
        } catch (Exception e) {
            return false;
        }
    }

    @Override
    public void create(byte[] challenge) throws Exception {
        delete();
        KeyPairGenerator generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, STORE);
        generator.initialize(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setAttestationChallenge(challenge)
                .build());
        generator.generateKeyPair();
    }

    @Override
    public String keyId() throws Exception {
        byte[] spki = keyStore().getCertificate(ALIAS).getPublicKey().getEncoded();
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(spki)) {
            hex.append(String.format("%02x", b));
        }
        return hex.toString();
    }

    @Override
    public List<String> attestation() throws Exception {
        List<String> out = new ArrayList<>();
        for (Certificate c : keyStore().getCertificateChain(ALIAS)) {
            out.add(Base64.encodeToString(c.getEncoded(), Base64.NO_WRAP));
        }
        return out;
    }

    @Override
    public String sign(byte[] data) throws Exception {
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initSign((PrivateKey) keyStore().getKey(ALIAS, null));
        signature.update(data);
        return Base64.encodeToString(signature.sign(), Base64.NO_WRAP);
    }

    @Override
    public void delete() throws Exception {
        KeyStore ks = keyStore();
        if (ks.containsAlias(ALIAS)) {
            ks.deleteEntry(ALIAS);
        }
    }
}
