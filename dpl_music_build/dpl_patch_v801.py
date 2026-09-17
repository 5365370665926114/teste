from pathlib import Path

ROOT = Path("Metrolist")


def replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Pattern not found in {path}: {old[:160]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Version bump for the second source-built test release.
build_gradle = ROOT / "app/build.gradle.kts"
replace_once(build_gradle, "versionCode = 800", "versionCode = 801")
replace_once(build_gradle, 'versionName = "8.0.0"', 'versionName = "8.0.1"')


# Improve the public offline exporter:
# - do not rewrite every completed track on every app startup if a valid public
#   copy already exists;
# - provide a safe Android DocumentsUI launcher for the public folder.
offline_storage = ROOT / "app/src/main/kotlin/com/metrolist/music/utils/PublicOfflineStorage.kt"
replace_once(
    offline_storage,
    "import android.content.Context\n",
    "import android.content.Context\nimport android.content.Intent\nimport android.provider.DocumentsContract\n",
)

replace_once(
    offline_storage,
    '''fun publicOfflineDisplayPath(context: Context): String =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        "Armazenamento interno/$PUBLIC_OFFLINE_RELATIVE_PATH"
    } else {
        val base = context.getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        File(base ?: context.filesDir, "DPL Music/Offline").absolutePath
    }

fun exportPublicOffline(''',
    '''fun publicOfflineDisplayPath(context: Context): String =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        "Armazenamento interno/$PUBLIC_OFFLINE_RELATIVE_PATH"
    } else {
        val base = context.getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        File(base ?: context.filesDir, "DPL Music/Offline").absolutePath
    }

fun openPublicOfflineFolder(context: Context): Boolean {
    val documentUri = DocumentsContract.buildDocumentUri(
        "com.android.externalstorage.documents",
        "primary:Music/DPL Music/Offline",
    )
    val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        putExtra(DocumentsContract.EXTRA_INITIAL_URI, documentUri)
    }
    return runCatching {
        context.startActivity(intent)
        true
    }.getOrDefault(false)
}

fun exportPublicOffline(''',
)

replace_once(
    offline_storage,
    '''    // Make re-downloads idempotent instead of producing (1), (2), ... copies.
    resolver.delete(
        collection,
        "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND ${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?",
        arrayOf(displayName, "%DPL Music/Offline%"),
    )

    val values = ContentValues().apply {''',
    '''    // Reconciliation runs on app startup. Keep an already complete public copy
    // instead of rewriting the same audio on every launch.
    val selection =
        "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND ${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
    val selectionArgs = arrayOf(displayName, "%DPL Music/Offline%")
    val alreadyComplete = resolver.query(
        collection,
        arrayOf(MediaStore.MediaColumns.SIZE),
        selection,
        selectionArgs,
        null,
    )?.use { cursor ->
        if (!cursor.moveToFirst()) {
            false
        } else {
            val size = cursor.getLong(0)
            size > 0L && (expectedLength == null || size >= expectedLength)
        }
    } ?: false
    if (alreadyComplete) return

    // Remove stale/partial copies before recreating the public file.
    resolver.delete(collection, selection, selectionArgs)

    val values = ContentValues().apply {''',
)


# Storage screen: make the displayed offline path actionable.
storage_settings = ROOT / "app/src/main/kotlin/com/metrolist/music/ui/screens/settings/StorageSettings.kt"
replace_once(
    storage_settings,
    "import com.metrolist.music.utils.publicOfflineDisplayPath\n",
    "import com.metrolist.music.utils.publicOfflineDisplayPath\nimport com.metrolist.music.utils.openPublicOfflineFolder\n",
)
replace_once(
    storage_settings,
    '''                    Material3SettingsItem(
                        icon = painterResource(R.drawable.storage),
                        title = { Text("Pasta de download offline") },
                        description = {
                            Text(
                                text = publicOfflineDisplayPath(context) +
                                    "\\nDownloads concluídos são salvos aqui automaticamente.",
                            )
                        },
                    ),''',
    '''                    Material3SettingsItem(
                        icon = painterResource(R.drawable.storage),
                        title = { Text("Pasta de download offline") },
                        description = {
                            Text(
                                text = publicOfflineDisplayPath(context) +
                                    "\\nDownloads concluídos são salvos aqui automaticamente. Toque para abrir.",
                            )
                        },
                        onClick = {
                            openPublicOfflineFolder(context)
                        },
                    ),''',
)

# Keep the shipped notice in sync with the build version.
notice = ROOT / "app/src/main/assets/dpl_source_notice.txt"
replace_once(notice, "DPL Music Ultra 8.0.0", "DPL Music Ultra 8.0.1")

print("DPL Music Ultra 8.0.1 improvements applied successfully")
