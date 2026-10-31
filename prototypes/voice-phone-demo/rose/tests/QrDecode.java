// Decodes QR PNGs with ZXing for rose/tests/test_qr.py.
//
//     java -cp .tools/zxing/core-3.5.4.jar QrDecode.java a.png b.png ...
//
// Prints one line per file: "OK <base64 of the UTF-8 text>" or "FAIL <reason>".

import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.RGBLuminanceSource;
import com.google.zxing.Result;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

import java.awt.image.BufferedImage;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.EnumMap;
import java.util.Map;
import javax.imageio.ImageIO;

public class QrDecode {
    public static void main(String[] args) {
        Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        hints.put(DecodeHintType.CHARACTER_SET, "UTF-8");
        for (String path : args) {
            try {
                BufferedImage img = ImageIO.read(new File(path));
                int w = img.getWidth(), h = img.getHeight();
                int[] pixels = img.getRGB(0, 0, w, h, null, 0, w);
                BinaryBitmap bitmap = new BinaryBitmap(new HybridBinarizer(new RGBLuminanceSource(w, h, pixels)));
                Result result = new QRCodeReader().decode(bitmap, hints);
                byte[] text = result.getText().getBytes(StandardCharsets.UTF_8);
                System.out.println("OK " + Base64.getEncoder().encodeToString(text));
            } catch (Exception e) {
                System.out.println("FAIL " + e.getClass().getSimpleName());
            }
        }
    }
}
