package rose.models.composer;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

/** Rose server's persistent local model child. One bounded JSON line in, one validated result out. No listener. */
public final class ComposerWorker {
    public static void main(String[] args) throws Exception {
        try (LlamaInference model = new LlamaInference(new File(args[0]));
             PrintWriter out = new PrintWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true)) {
            model.prepare();
            Map<String,Object> ready = new LinkedHashMap<String,Object>();
            ready.put("status", "ready"); ready.put("model", model.info()); out.println(Json.write(ready));
            Composer composer = new Composer(Catalog.cat1(), model);
            BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
            StringBuilder line = new StringBuilder(); int c;
            while ((c = in.read()) != -1) {
                if (c != '\n') { if (line.length() >= 256 * 1024) return; line.append((char)c); continue; }
                Object request;
                try { request = Json.parse(line.toString()); }
                catch (Json.ParseError e) { request = null; }
                line.setLength(0);
                out.println(composer.compose(request).toJson());
                if (out.checkError()) return;
            }
        } catch (Throwable failure) {
            // No prompts, model diagnostics, paths or arbitrary exception text cross this protocol.
            System.out.println("{\"status\":\"failed\",\"failure\":\"model_unavailable\"}");
        }
    }
}
