/**
 * Optional Google Apps Script -> GitHub Issues Studio intake bridge.
 * It creates INBOX requests only; no code, canon or owner approval is granted.
 *
 * Script Properties (set manually; NEVER commit credentials):
 * STUDIO_DRIVE_INTAKE_FOLDER_ID = one owner-controlled Google Drive folder ID
 * STUDIO_GITHUB_ISSUES_TOKEN = fine-grained PAT with Issues write on TheNomad_Studio
 * STUDIO_GITHUB_REPO = SIGAAMDAD/TheNomad_Studio
 */
function studioSyncDriveIntake() {
  var props = PropertiesService.getScriptProperties();
  var folderId = props.getProperty("STUDIO_DRIVE_INTAKE_FOLDER_ID");
  var token = props.getProperty("STUDIO_GITHUB_ISSUES_TOKEN");
  var repo = props.getProperty("STUDIO_GITHUB_REPO") || "SIGAAMDAD/TheNomad_Studio";
  if (!folderId || !token || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) {
    throw new Error("Missing/invalid required Studio Drive/GitHub Script Properties");
  }
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    return; // another worker owns this intake run
  }
  try {
    var files = DriveApp.getFolderById(folderId).getFiles();
    var pending = [];
    while (files.hasNext()) {
      var f = files.next();
      if (!/^\[STUDIO\] /.test(f.getName())) {
        continue;
      }
      if (props.getProperty("drive:" + f.getId())) {
        continue;
      }
      pending.push(f);
      if (pending.length > 50) {
        throw new Error("Too many new Studio documents; split intake into smaller batches");
      }
    }
    if (!pending.length) {
      return;
    }
    var issues = studioFetchAllIssues_(repo, token);
    pending.forEach(function (file) {
      var marker = "<!-- studio-drive-file-id:" + file.getId() + " -->";
      var existing = issues.filter(function (issue) {
        return (issue.body || "").indexOf(marker) >= 0;
      });
      if (existing.length > 1) {
        throw new Error("Duplicate Studio Drive references for file " + file.getId());
      }
      if (existing.length === 1) {
        props.setProperty("drive:" + file.getId(), String(existing[0].number));
        return;
      }
      var body = "## Drive-origin Studio request\n\n" +
        "Source document: " + file.getUrl() + "\n\n" +
        "The owner must scope and apply a trusted studio:approved label before any production begins.\n\n" +
        marker;
      var response = studioRequest_(repo, token, "/issues", "post", {
        title: file.getName().replace(/^\[STUDIO\] /, "[Studio] "),
        body: body
      });
      if (!response.number) {
        throw new Error("Issue creation did not return an actual issue number");
      }
      props.setProperty("drive:" + file.getId(), String(response.number));
    });
  } finally {
    lock.releaseLock();
  }
}

function studioFetchAllIssues_(repo, token) {
  var found = [];
  for (var page = 1; page <= 20; page++) {
    var list = studioRequest_(repo, token, "/issues?state=all&per_page=100&page=" + page, "get");
    if (!Array.isArray(list)) {
      throw new Error("Unexpected GitHub Issues response");
    }
    list.forEach(function (issue) {
      if (!issue.pull_request) {
        found.push(issue);
      }
    });
    if (list.length < 100) {
      return found;
    }
  }
  throw new Error("Issue pagination cap reached; refusing unverified intake");
}

function studioRequest_(repo, token, path, method, body) {
  var options = {
    method: method,
    headers: {
      "Authorization": "Bearer " + token,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28"
    },
    muteHttpExceptions: true
  };
  if (body) {
    options.contentType = "application/json";
    options.payload = JSON.stringify(body);
  }
  var response = UrlFetchApp.fetch("https://api.github.com/repos/" + repo + path, options);
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) {
    throw new Error("GitHub intake HTTP " + response.getResponseCode() +
                    ": " + response.getContentText().slice(0, 300));
  }
  return JSON.parse(response.getContentText());
}

/** Run once manually after supplying Script Properties and granting permissions. */
function studioInstallDriveIntakeTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "studioSyncDriveIntake") {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  ScriptApp.newTrigger("studioSyncDriveIntake").timeBased().everyMinutes(15).create();
}
