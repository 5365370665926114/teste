from pathlib import Path

ROOT = Path("Metrolist")


def replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Pattern not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# -----------------------------------------------------------------------------
# Versioning: this is a clean-install DPL build with a package id controlled by
# the workflow. Version code is intentionally above the uploaded 7.1.0 build.
# -----------------------------------------------------------------------------
build_gradle = ROOT / "app/build.gradle.kts"
replace_once(build_gradle, 'versionCode = 153', 'versionCode = 800')
replace_once(build_gradle, 'versionName = "13.7.0"', 'versionName = "8.0.0"')


# -----------------------------------------------------------------------------
# Public offline storage mirror.
# Metrolist/Media3 keeps canonical offline data in SimpleCache. This helper
# publishes a real, user-visible copy after each completed download, without
# broad MANAGE_EXTERNAL_STORAGE access on modern Android.
# -----------------------------------------------------------------------------
offline_storage = ROOT / "app/src/main/kotlin/com/metrolist/music/utils/PublicOfflineStorage.kt"
offline_storage.write_text(r'''/**
 * DPL Music Ultra public offline storage helper.
 * Based on Metrolist's Media3 download cache; exported files are a public mirror
 * so users can see and use completed downloads from a normal file manager.
 */
package com.metrolist.music.utils

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.media3.datasource.cache.Cache
import com.metrolist.music.db.MusicDatabase
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.OutputStream

private const val PUBLIC_OFFLINE_RELATIVE_PATH = "Music/DPL Music/Offline"

fun publicOfflineDisplayPath(context: Context): String =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        "Armazenamento interno/$PUBLIC_OFFLINE_RELATIVE_PATH"
    } else {
        val base = context.getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        File(base ?: context.filesDir, "DPL Music/Offline").absolutePath
    }

fun exportPublicOffline(
    context: Context,
    database: MusicDatabase,
    downloadCache: Cache,
    songId: String,
) {
    val song = database.getSongByIdBlocking(songId) ?: return
    val mimeType = song.format?.mimeType?.substringBefore(';')?.trim().orEmpty().ifBlank { "audio/webm" }
    val extension = extensionForMime(mimeType)
    val artist = song.orderedArtists.joinToString(", ") { it.name }.ifBlank { "Unknown Artist" }
    val album = song.album?.title.orEmpty()
    val title = song.song.title.ifBlank { "Unknown Track" }
    val displayName = "${sanitizeFilePart(artist)} - ${sanitizeFilePart(title)}.$extension"
    val expectedLength = song.format?.contentLength?.takeIf { it > 0L }

    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        exportViaMediaStore(
            context = context,
            downloadCache = downloadCache,
            songId = songId,
            displayName = displayName,
            mimeType = mimeType,
            title = title,
            artist = artist,
            album = album,
            expectedLength = expectedLength,
        )
    } else {
        exportToAppExternalMusic(
            context = context,
            downloadCache = downloadCache,
            songId = songId,
            displayName = displayName,
            expectedLength = expectedLength,
        )
    }
}

fun deletePublicOffline(
    context: Context,
    database: MusicDatabase,
    songId: String,
) {
    val song = database.getSongByIdBlocking(songId) ?: return
    val mimeType = song.format?.mimeType?.substringBefore(';')?.trim().orEmpty().ifBlank { "audio/webm" }
    val extension = extensionForMime(mimeType)
    val artist = song.orderedArtists.joinToString(", ") { it.name }.ifBlank { "Unknown Artist" }
    val title = song.song.title.ifBlank { "Unknown Track" }
    val displayName = "${sanitizeFilePart(artist)} - ${sanitizeFilePart(title)}.$extension"

    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        val resolver = context.contentResolver
        val collection = MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        resolver.delete(
            collection,
            "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND ${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?",
            arrayOf(displayName, "%DPL Music/Offline%"),
        )
    } else {
        val base = context.getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        val folder = File(base ?: context.filesDir, "DPL Music/Offline")
        File(folder, displayName).delete()
    }
}

private fun exportViaMediaStore(
    context: Context,
    downloadCache: Cache,
    songId: String,
    displayName: String,
    mimeType: String,
    title: String,
    artist: String,
    album: String,
    expectedLength: Long?,
) {
    val resolver = context.contentResolver
    val collection = MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)

    // Make re-downloads idempotent instead of producing (1), (2), ... copies.
    resolver.delete(
        collection,
        "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND ${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?",
        arrayOf(displayName, "%DPL Music/Offline%"),
    )

    val values = ContentValues().apply {
        put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
        put(MediaStore.MediaColumns.MIME_TYPE, mimeType)
        put(MediaStore.MediaColumns.RELATIVE_PATH, PUBLIC_OFFLINE_RELATIVE_PATH)
        put(MediaStore.MediaColumns.IS_PENDING, 1)
        put(MediaStore.Audio.Media.TITLE, title)
        put(MediaStore.Audio.Media.ARTIST, artist)
        if (album.isNotBlank()) put(MediaStore.Audio.Media.ALBUM, album)
    }

    val uri = resolver.insert(collection, values)
        ?: throw IOException("Unable to create public offline media entry")

    try {
        val written = resolver.openOutputStream(uri, "w")?.use { output ->
            writeCachedTrack(downloadCache, songId, output)
        } ?: throw IOException("Unable to open output stream for public offline media")

        if (expectedLength != null && written < expectedLength) {
            throw IOException("Offline cache is incomplete: wrote $written of $expectedLength bytes")
        }

        val ready = ContentValues().apply {
            put(MediaStore.MediaColumns.IS_PENDING, 0)
            put(MediaStore.MediaColumns.SIZE, written)
        }
        resolver.update(uri, ready, null, null)
    } catch (t: Throwable) {
        resolver.delete(uri, null, null)
        throw t
    }
}

private fun exportToAppExternalMusic(
    context: Context,
    downloadCache: Cache,
    songId: String,
    displayName: String,
    expectedLength: Long?,
) {
    val base = context.getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        ?: throw IOException("External music directory is unavailable")
    val folder = File(base, "DPL Music/Offline")
    if (!folder.exists() && !folder.mkdirs()) {
        throw IOException("Unable to create offline folder: ${folder.absolutePath}")
    }

    val destination = File(folder, displayName)
    val temp = File(folder, ".$displayName.part")
    if (temp.exists()) temp.delete()

    try {
        val written = FileOutputStream(temp).use { output ->
            writeCachedTrack(downloadCache, songId, output)
        }
        if (expectedLength != null && written < expectedLength) {
            throw IOException("Offline cache is incomplete: wrote $written of $expectedLength bytes")
        }
        if (destination.exists()) destination.delete()
        if (!temp.renameTo(destination)) {
            throw IOException("Unable to finalize offline file")
        }
    } catch (t: Throwable) {
        temp.delete()
        throw t
    }
}

private fun writeCachedTrack(cache: Cache, songId: String, output: OutputStream): Long {
    val spans = cache.getCachedSpans(songId).sortedBy { it.position }
    if (spans.isEmpty()) throw IOException("No cached spans for $songId")

    var expectedPosition = 0L
    var written = 0L
    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)

    for (span in spans) {
        if (span.position != expectedPosition) {
            throw IOException("Offline cache has a gap at $expectedPosition (next=${span.position})")
        }
        val source = span.file ?: throw IOException("Cached span has no backing file")
        var remaining = span.length
        FileInputStream(source).use { input ->
            while (remaining > 0L) {
                val read = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                if (read < 0) throw IOException("Unexpected EOF while exporting cached audio")
                output.write(buffer, 0, read)
                written += read
                remaining -= read
            }
        }
        expectedPosition += span.length
    }
    output.flush()
    return written
}

private fun extensionForMime(mimeType: String): String = when {
    mimeType.contains("webm", ignoreCase = true) -> "webm"
    mimeType.contains("mp4", ignoreCase = true) || mimeType.contains("m4a", ignoreCase = true) -> "m4a"
    mimeType.contains("mpeg", ignoreCase = true) || mimeType.contains("mp3", ignoreCase = true) -> "mp3"
    mimeType.contains("ogg", ignoreCase = true) || mimeType.contains("opus", ignoreCase = true) -> "ogg"
    else -> "webm"
}

private fun sanitizeFilePart(value: String): String =
    value
        .replace(Regex("""[\\/:*?\"<>|\p{Cntrl}]"""), "_")
        .trim()
        .ifBlank { "Unknown" }
        .take(120)
''', encoding="utf-8")


# -----------------------------------------------------------------------------
# Hook public export into Media3's real download lifecycle.
# -----------------------------------------------------------------------------
download_util = ROOT / "app/src/main/kotlin/com/metrolist/music/playback/DownloadUtil.kt"
replace_once(
    download_util,
    'import com.metrolist.music.utils.InnerTubeXPlayer\n',
    'import com.metrolist.music.utils.InnerTubeXPlayer\nimport com.metrolist.music.utils.deletePublicOffline\nimport com.metrolist.music.utils.exportPublicOffline\n',
)
replace_once(
    download_util,
    '''                                Download.STATE_COMPLETED -> {\n                                    removeFromPlayerCache(download.request.id)\n                                    database.updateDownloadedInfo(download.request.id, true, LocalDateTime.now())\n                                }''',
    '''                                Download.STATE_COMPLETED -> {\n                                    removeFromPlayerCache(download.request.id)\n                                    database.updateDownloadedInfo(download.request.id, true, LocalDateTime.now())\n                                    runCatching {\n                                        exportPublicOffline(context, database, downloadCache, download.request.id)\n                                    }.onFailure { error ->\n                                        Timber.tag(TAG).e(error, "Failed to export ${download.request.id} to public offline folder")\n                                    }\n                                }''',
)
replace_once(
    download_util,
    '''                    override fun onDownloadRemoved(\n                        downloadManager: DownloadManager,\n                        download: Download,\n                    ) {\n                        val downloadId = download.request.id\n                        songUrlCache.invalidate(downloadId)\n\n                        runCatching {\n                            database.updateDownloadedInfo(downloadId, false, null)\n                        }.onSuccess {''',
    '''                    override fun onDownloadRemoved(\n                        downloadManager: DownloadManager,\n                        download: Download,\n                    ) {\n                        val downloadId = download.request.id\n                        songUrlCache.invalidate(downloadId)\n\n                        runCatching {\n                            deletePublicOffline(context, database, downloadId)\n                        }.onFailure { error ->\n                            Timber.tag(TAG).w(error, "Failed to remove public offline file for $downloadId")\n                        }\n\n                        runCatching {\n                            database.updateDownloadedInfo(downloadId, false, null)\n                        }.onSuccess {''',
)
replace_once(
    download_util,
    '''        scope.launch {\n            result.values\n                .filter { it.state == Download.STATE_COMPLETED }\n                .forEach { removeFromPlayerCache(it.request.id) }\n        }''',
    '''        scope.launch {\n            result.values\n                .filter { it.state == Download.STATE_COMPLETED }\n                .forEach { download ->\n                    removeFromPlayerCache(download.request.id)\n                    runCatching {\n                        exportPublicOffline(context, database, downloadCache, download.request.id)\n                    }.onFailure { error ->\n                        Timber.tag(TAG).w(error, "Failed to reconcile public offline file for ${download.request.id}")\n                    }\n                }\n        }''',
)


# -----------------------------------------------------------------------------
# Storage UI: show the real offline folder and clear downloads via DownloadManager
# instead of deleting cache files behind Media3's back.
# -----------------------------------------------------------------------------
storage_settings = ROOT / "app/src/main/kotlin/com/metrolist/music/ui/screens/settings/StorageSettings.kt"
replace_once(
    storage_settings,
    'import androidx.navigation.NavController\n',
    'import androidx.navigation.NavController\nimport androidx.media3.exoplayer.offline.DownloadService\n',
)
replace_once(
    storage_settings,
    'import com.metrolist.music.extensions.tryOrNull\n',
    'import com.metrolist.music.extensions.tryOrNull\nimport com.metrolist.music.playback.ExoDownloadService\n',
)
replace_once(
    storage_settings,
    'import com.metrolist.music.utils.rememberPreference\n',
    'import com.metrolist.music.utils.rememberPreference\nimport com.metrolist.music.utils.publicOfflineDisplayPath\n',
)
replace_once(
    storage_settings,
    '''            onConfirm = {\n                coroutineScope.launch(Dispatchers.IO) {\n                    downloadCache.keys.forEach { key ->\n                        downloadCache.removeResource(key)\n                    }\n                }\n                clearDownloads = false\n            },''',
    '''            onConfirm = {\n                DownloadService.sendRemoveAllDownloads(\n                    context,\n                    ExoDownloadService::class.java,\n                    false,\n                )\n                clearDownloads = false\n            },''',
)
replace_once(
    storage_settings,
    '''                    Material3SettingsItem(\n                        icon = painterResource(R.drawable.storage),\n                        title = { Text(stringResource(R.string.downloaded_songs)) },\n                        description = {\n                            Text(text = Formatter.formatShortFileSize(context, downloadCacheSize))\n                        },\n                    ),\n                    Material3SettingsItem(''',
    '''                    Material3SettingsItem(\n                        icon = painterResource(R.drawable.storage),\n                        title = { Text(stringResource(R.string.downloaded_songs)) },\n                        description = {\n                            Text(text = Formatter.formatShortFileSize(context, downloadCacheSize))\n                        },\n                    ),\n                    Material3SettingsItem(\n                        icon = painterResource(R.drawable.storage),\n                        title = { Text("Pasta de download offline") },\n                        description = {\n                            Text(\n                                text = publicOfflineDisplayPath(context) +\n                                    "\\nDownloads concluídos são salvos aqui automaticamente.",\n                            )\n                        },\n                    ),\n                    Material3SettingsItem(''',
)


# Source/branding notice shipped with the app's assets.
notice = ROOT / "app/src/main/assets/dpl_source_notice.txt"
notice.parent.mkdir(parents=True, exist_ok=True)
notice.write_text(
    "DPL Music Ultra 8.0.0 is based on Metrolist (GPL-3.0), pinned at commit "
    "6be77c1db3f515dfe3512f581931bdfadd3f68d4. Offline/public-storage design "
    "also references SpotiFlyer's documented foreground-download/storage model (GPL-3.0).\n",
    encoding="utf-8",
)

print("DPL Music Ultra patches applied successfully")
