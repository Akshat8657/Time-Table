// Stops TimeTablePro with NO window at all (no black window, no flash). Status messages go to logs\stop.log.
// You do not double-click this file: use the "Stop TimeTablePro" shortcut in the main folder (it runs this file).
// A message box appears ONLY if the stop really fails.
var sh = new ActiveXObject("WScript.Shell");
var fso = new ActiveXObject("Scripting.FileSystemObject");
var root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName));   // main project folder
var logs = root + "\\logs";
var log = logs + "\\stop.log";
// every launch first writes to its OWN private file, so two launches at the same time can never block each other
var runLog = logs + "\\stop." + new Date().getTime() + "." + Math.floor(Math.random() * 100000) + ".run";
var code = 1;

function readAll(path) {
  try {
    if (fso.FileExists(path) && fso.GetFile(path).Size > 0) { var f = fso.OpenTextFile(path, 1); var t = f.ReadAll(); f.Close(); return t; }
  } catch (e) {}
  return "";
}
function appendToLog(text) {                       // never fatal: if the log is busy we just try again a few times
  for (var i = 0; i < 15; i++) {
    try { var f = fso.OpenTextFile(log, 8, true); f.Write(text); f.Close(); return true; } catch (e) { WScript.Sleep(200); }
  }
  return false;
}

function sweepOldRuns() {                          // removes old private run files (only those nobody holds open any more)
  try {
    var e = new Enumerator(fso.GetFolder(logs).Files);
    for (; !e.atEnd(); e.moveNext()) {
      var f = e.item();
      if (/\.run$/i.test(f.Name) && (new Date().getTime() - new Date(f.DateLastModified).getTime()) > 5 * 60 * 1000) { try { f.Delete(true); } catch (x) {} }
    }
  } catch (x) {}
}

try {
  if (!fso.FolderExists(logs)) fso.CreateFolder(logs);
  if (fso.FileExists(log) && fso.GetFile(log).Size > 300000) { try { fso.DeleteFile(log, true); } catch (e) {} }   // keep the log small
  sh.CurrentDirectory = root;
  sh.Environment("PROCESS")("TT_SILENT") = "1";
  code = sh.Run('cmd.exe /d /s /c ""' + root + '\\stop.bat" > "' + runLog + '" 2>&1"', 0, true);
} catch (e) {
  code = 1;
}

appendToLog("\r\n=== " + new Date().toLocaleString() + " : Stop TimeTablePro (finished, exit code " + code + ") ===\r\n" + readAll(runLog));
try { if (fso.FileExists(runLog)) fso.DeleteFile(runLog, true); } catch (e) {}
sweepOldRuns();                                    // the servers are down now, so old start files are free to remove

// (TT_NO_POPUP=1 is used when Claude Code stops the app for you: then a failure is only reported by the exit code + logs)
if (code !== 0 && sh.Environment("PROCESS")("TT_NO_POPUP") !== "1") {
  sh.Popup("TimeTablePro band nahi ho paya.\n\n" +
           "Details yahan likhi hain:\n    " + log + "\n" +
           "Ye file Claude ko dikhao.",
           0, "TimeTablePro", 16);
}
WScript.Quit(code);
