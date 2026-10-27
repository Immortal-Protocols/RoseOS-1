package dev.pixel.voice;

/** Keep OS-signed app bridges separate from the app-suite signing identity. */
final class AgentAppTrust {
 static final String CONTACTS_CERT="3f6b321c9fef5ef726c9c6ff28528671a9ce9b92139ebaa31708311511bdf3b4";
 static final String GALLERY_CERT="9d39623d6d9e5efafc83a6e06d92c5e9ea956bd40423a4efab39ba494908d5fd";
 static final String SKY_CERT="2a9f8b9ab942113cea8462b0ca41f6614c5649e016e5903309ce3ecabc3f1fca";
 static boolean allows(String packageName,boolean sameSigner,String[] currentCertificates){
  if(sameSigner)return true;
  if("dev.pixel.sky".equals(packageName))return currentCertificates!=null&&currentCertificates.length==1&&SKY_CERT.equals(currentCertificates[0]);
  String expected="com.android.contacts".equals(packageName)?CONTACTS_CERT:
      "com.android.gallery3d".equals(packageName)?GALLERY_CERT:null;
  return expected!=null&&currentCertificates!=null&&currentCertificates.length==1&&expected.equals(currentCertificates[0]);
 }
 private AgentAppTrust(){}
}
