package dev.pixel.models;

import java.util.List;

/**
 * The install-scoped, non-exportable EC P-256 device key of service-auth-v1. Android Keystore in the
 * app (KeystoreDeviceKey); a software key in tests.
 */
public interface DeviceKey {
    boolean exists();

    /** Deletes any key and creates a new one whose attestation carries {@code challenge}. */
    void create(byte[] challenge) throws Exception;

    /** Lowercase hex SHA-256 of the DER SubjectPublicKeyInfo. */
    String keyId() throws Exception;

    /** Base64 DER certificates, leaf first, root last. */
    List<String> attestation() throws Exception;

    /** Base64 DER SHA256withECDSA over {@code data}. */
    String sign(byte[] data) throws Exception;

    /** Only on an explicitly selected reset. */
    void delete() throws Exception;
}
