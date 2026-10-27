package dev.pixel.voice;
import android.app.*;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.util.concurrent.*;

/** A pairing code must come from the Mac; app copies cannot enroll themselves. */
public final class PairingActivity extends Activity{
 private final ExecutorService io=Executors.newSingleThreadExecutor();
 @Override public void onCreate(Bundle saved){
  super.onCreate(saved);getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE,WindowManager.LayoutParams.FLAG_SECURE);
  LinearLayout content=new LinearLayout(this);content.setOrientation(1);int pad=(int)(24*getResources().getDisplayMetrics().density);content.setPadding(pad,pad*2,pad,pad);setContentView(content);
  TextView title=new TextView(this);title.setText("Pair with your Mac");title.setTextSize(27);content.addView(title);
  TextView description=new TextView(this);description.setText("Enter the one-time code shown on your Mac. This phone’s private key stays in its secure hardware.");description.setTextSize(17);content.addView(description);
  EditText code=new EditText(this);code.setHint("XXXX-XXXX-XXXX");code.setSingleLine(true);code.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS|android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);code.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);content.addView(code);
  Button pair=new Button(this);pair.setText("Pair this Pixel");content.addView(pair);
  TextView status=new TextView(this);content.addView(status);
  pair.setOnClickListener(v->{String entered=code.getText().toString().trim();if(!entered.replace("-","").replace(" ","").matches("[A-Za-z2-9]{12}")){status.setText("Enter the 12-character code from your Mac.");return;}pair.setEnabled(false);status.setText("Verifying secure connection…");io.execute(()->{
   try{new DeviceIdentity(this).pair(entered);runOnUiThread(()->{if(isFinishing())return;status.setText("Paired securely. Your private key stays on this Pixel.");code.setText("");pair.setText("Done");pair.setEnabled(true);pair.setOnClickListener(done->finish());});}
   catch(Exception e){runOnUiThread(()->{if(!isFinishing()){status.setText(e.getMessage());pair.setEnabled(true);}});}
  });});
 }
 @Override public void onDestroy(){io.shutdownNow();super.onDestroy();}
}
