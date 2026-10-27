package dev.pixel.voice;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.net.URI;
import org.json.JSONObject;

/** Uses the existing pinned connection and keeps a one-step server rollback. */
public final class ServerConnectionActivity extends Activity {
 private EditText address;
 private TextView status;
 private SharedPreferences preferences;

 @Override public void onCreate(Bundle saved) {
  super.onCreate(saved);
  preferences=getSharedPreferences("MainActivity",0);
  LinearLayout page=new LinearLayout(this);page.setOrientation(LinearLayout.VERTICAL);
  int padding=(int)(24*getResources().getDisplayMetrics().density);
  page.setPadding(padding,padding*2,padding,padding);setContentView(page);
  TextView title=new TextView(this);title.setText("Server connection");title.setTextSize(27);page.addView(title);
  TextView description=new TextView(this);description.setText("Choose the server this phone connects to. Your existing device pairing stays in place.");description.setTextSize(16);page.addView(description);
  Button conversation=new Button(this);conversation.setText("Conversation mode");conversation.setOnClickListener(v->startActivity(new android.content.Intent(this,ConversationModeActivity.class)));page.addView(conversation);
  address=new EditText(this);address.setSingleLine(true);address.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);address.setHint("https://server:port");address.setContentDescription("Server address");
  VoiceService service=VoiceService.instance;
  address.setText(service==null?preferences.getString("endpoint",""):service.endpoint);page.addView(address);
  status=new TextView(this);page.addView(status);
  Button connect=new Button(this);connect.setText("Connect");connect.setOnClickListener(v->connect(address.getText().toString().trim()));page.addView(connect);
  String previous=preferences.getString("previous-endpoint","");
  if(!previous.isEmpty()) {
   Button restore=new Button(this);restore.setText("Use previous server");restore.setOnClickListener(v->connect(previous));page.addView(restore);
  }
 }

 private void connect(String endpoint) {
  try {
   URI uri=new URI(endpoint);
   if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null||!(uri.getPath()==null||uri.getPath().isEmpty())||!endpoint.matches("https://[A-Za-z0-9.:-]+"))throw new IllegalArgumentException("Enter an HTTPS server address, including its port if needed.");
   VoiceService service=VoiceService.instance;
   if(service==null){status.setText("Open Rose Agent and try again.");return;}
   String previous=service.endpoint;
   if(!previous.equals(endpoint))preferences.edit().putString("previous-endpoint",previous).apply();
   service.setServerEndpoint(endpoint);
   service.web.evaluateJavascript("document.getElementById('address').value="+JSONObject.quote(endpoint)+";check();",null);
   finish();
  } catch(Exception error) {
   status.setText(error.getMessage()==null?"Could not change the server address.":error.getMessage());
  }
 }
}
