// KlayoutClaw's native bridge. AutoCAD APIs run on its application thread.
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#include <windef.h>
#include <adslib.h>
#include <aced.h>
#include <rxregsvc.h>
#include <dbapserv.h>
#include <acdocman.h>
#include <dbmain.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <chrono>
#include <map>

static std::atomic<bool> running{false};
static int listener = -1;
static std::thread worker;
static std::string socketPath, runtimePath;
static constexpr size_t MAX_MESSAGE = 64 * 1024 * 1024;
static std::map<AcApDocument *, std::string> documentIds;

static void assignDocumentId(AcApDocument *doc) {
    documentIds[doc] = [NSUUID.UUID.UUIDString UTF8String];
}
class DocumentLifetime final : public AcApDocManagerReactor {
public:
    void documentCreateStarted(AcApDocument *doc) override { assignDocumentId(doc); }
    void documentToBeDestroyed(AcApDocument *doc) override { documentIds.erase(doc); }
    void documentCreateCanceled(AcApDocument *doc) override { documentIds.erase(doc); }
};
static DocumentLifetime lifetime;

static std::wstring toWide(NSString *s) {
    NSData *data = [s dataUsingEncoding:NSUTF32LittleEndianStringEncoding];
    return std::wstring((const wchar_t *)data.bytes, data.length / sizeof(wchar_t));
}
static NSString *utf8(const wchar_t *s) {
    if (!s) return @"";
    return [[NSString alloc] initWithBytes:s length:wcslen(s) * sizeof(wchar_t)
                                encoding:NSUTF32LittleEndianStringEncoding] ?: @"";
}
static NSString *quote(NSString *s) {
    s = [s stringByReplacingOccurrencesOfString:@"\\" withString:@"\\\\"];
    s = [s stringByReplacingOccurrencesOfString:@"\"" withString:@"\\\""];
    s = [s stringByReplacingOccurrencesOfString:@"\n" withString:@"\\n"];
    return [@"\"" stringByAppendingString:[s stringByAppendingString:@"\""]];
}
static NSString *documentId(AcApDocument *doc) {
    if (!documentIds.count(doc)) assignDocumentId(doc);
    return [NSString stringWithFormat:@"%d:%s", getpid(), documentIds[doc].c_str()];
}
static NSDictionary *failure(NSString *message) { return @{@"error": message}; }
static NSDictionary *docInfo(AcApDocument *doc) {
    return @{@"id": documentId(doc), @"name": utf8(doc->docTitle()),
             @"path": utf8(doc->fileName()), @"active": @(doc == acDocManager->mdiActiveDocument()),
             @"units": @(doc->database()->insunits())};
}

static AcApDocument *findOpenPath(NSString *path) {
    NSString *target = path.stringByResolvingSymlinksInPath;
    AcApDocument *match = nullptr;
    auto *it = acDocManager->newAcApDocumentIterator();
    for (; !it->done(); it->step()) {
        auto *doc = it->document();
        if ([utf8(doc->fileName()).stringByResolvingSymlinksInPath isEqualToString:target]) {
            match = doc;
            break;
        }
    }
    delete it;
    return match;
}

struct Work {
    std::mutex mutex;
    std::condition_variable cv;
    bool done = false, cancelled = false, started = false;
    id __strong result = nil;
    std::function<id()> action;
};

// A timed-out work item owns its memory until its dispatched block returns.
static id onMain(std::function<id()> fn) {
    auto work = std::make_shared<Work>();
    work->action = std::move(fn);
    // A Cocoa main-queue callback can still be nested inside AutoCAD's input
    // loop. Schedule the CAD work through ObjectARX's application-context
    // scheduler and return from the Cocoa callback before invoking CAD APIs.
    auto callback = +[](void *raw) {
        std::unique_ptr<std::shared_ptr<Work>> owner(static_cast<std::shared_ptr<Work> *>(raw));
        auto item = *owner;
        {
            std::lock_guard<std::mutex> guard(item->mutex);
            if (item->cancelled) return;
            item->started = true;
        }
        id result;
        if (auto *active = acDocManager->mdiActiveDocument()) acDocManager->setCurDocument(active);
        @try { result = item->action(); }
        @catch (NSException *e) { result = failure(e.reason ?: @"Native exception"); }
        {
            std::lock_guard<std::mutex> guard(item->mutex);
            item->result = result;
            item->done = true;
        }
        item->cv.notify_one();
    };
    dispatch_async(dispatch_get_main_queue(), ^{
        auto *owner = new std::shared_ptr<Work>(work);
        auto status = acDocManager->beginExecuteInApplicationContext(callback, owner);
        if (status != Acad::eOk) {
            delete owner;
            {
                std::lock_guard<std::mutex> guard(work->mutex);
                work->result = failure(@"Cannot schedule work in AutoCAD application context");
                work->done = true;
            }
            work->cv.notify_one();
        }
    });
    std::unique_lock<std::mutex> guard(work->mutex);
    if (!work->cv.wait_for(guard, std::chrono::seconds(60), [&] { return work->done; })) {
        work->cancelled = true;
        return @{@"error": @"AutoCAD application-context request timed out; no automatic retry",
                 @"indeterminate": @(work->started)};
    }
    return work->result ?: [NSNull null];
}

static id handle(NSString *method, NSDictionary *params) {
    if ([method isEqualToString:@"ping"])
        return @{@"bridge": @"KlayoutClaw AutoCAD", @"version": @"0.1.0", @"pid": @(getpid()),
                 @"socket": @(socketPath.c_str()), @"transport": @"unix-socket", @"ui_automation": @NO};
    if ([method isEqualToString:@"documents"]) return onMain([]() -> id {
        NSMutableArray *result = [NSMutableArray array];
        auto *it = acDocManager->newAcApDocumentIterator();
        for (; !it->done(); it->step()) [result addObject:docInfo(it->document())];
        delete it;
        return result;
    });
    if ([method isEqualToString:@"activate_document"] || [method isEqualToString:@"close_document"])
        return onMain([=]() -> id {
            if (![params[@"document_id"] isKindOfClass:[NSString class]])
                return failure(@"document_id is required");
            AcApDocument *target = nullptr;
            auto *it = acDocManager->newAcApDocumentIterator();
            for (; !it->done(); it->step())
                if ([params[@"document_id"] isEqualToString:documentId(it->document())]) target = it->document();
            delete it;
            if (!target) return failure(@"Document identity no longer exists");
            resbuf state;
            if (acedGetVar(L"CMDACTIVE", &state) != RTNORM || state.resval.rint != 0)
                return failure(@"Finish the current AutoCAD command before switching or closing drawings");
            if ([method isEqualToString:@"activate_document"]) {
                auto status = acDocManager->activateDocument(target);
                return status == Acad::eOk ? @{@"queued": @YES} : failure(@"Cannot activate drawing");
            }
            if (target != acDocManager->mdiActiveDocument()) return failure(@"Activate the expected drawing before closing it");
            if (acedGetVar(L"DBMOD", &state) != RTNORM || state.resval.rint != 0)
                return failure(@"Save the drawing before closing it through the bridge");
            auto status = acDocManager->appContextCloseDocument(target);
            return status == Acad::eOk ? @{@"queued": @YES} : failure(@"Cannot close drawing");
        });
    if ([method isEqualToString:@"open_document"])
        return onMain([=]() -> id {
            NSString *path = params[@"path"];
            if (![path isKindOfClass:[NSString class]] || !path.isAbsolutePath)
                return failure(@"An absolute drawing path is required");
            if (!acDocManager->isApplicationContext()) return failure(@"AutoCAD is busy in document context");
            resbuf state;
            if (acDocManager->mdiActiveDocument() &&
                (acedGetVar(L"CMDACTIVE", &state) != RTNORM || state.resval.rint != 0))
                return failure(@"Finish the current AutoCAD command before creating or opening a drawing");
            if (findOpenPath(path)) return failure(@"This drawing is already open. Use a separate task copy.");
            auto status = acDocManager->appContextOpenDocument(toWide(path).c_str());
            if (status != Acad::eOk) return failure([NSString stringWithFormat:@"Document operation failed: %d", status]);
            return @{@"queued": @YES};
        });
    if ([method isEqualToString:@"save_document"]) return onMain([=]() -> id {
        NSString *path = params[@"path"];
        NSString *format = params[@"format"];
        if (![path isKindOfClass:[NSString class]] || !path.isAbsolutePath
            || ![format isKindOfClass:[NSString class]]
            || (![format isEqualToString:@"dwg"] && ![format isEqualToString:@"dxf"]))
            return failure(@"An absolute DWG or DXF output path is required");
        auto *doc = acDocManager->mdiActiveDocument();
        if (!doc) return failure(@"No active drawing");
        if (![documentId(doc) isEqualToString:params[@"document_id"]]) return failure(@"Active document mismatch");
        if (doc->database()->insunits() != 13) return failure(@"The drawing must use micrometre units");
        resbuf state;
        if (acedGetVar(L"CMDACTIVE", &state) != RTNORM || state.resval.rint != 0)
            return failure(@"Finish the current AutoCAD command before saving");
        if (auto *other = findOpenPath(path); other && other != doc)
            return failure(@"The output path belongs to a different open drawing");
        auto status = acDocManager->lockDocument(doc, AcAp::kWrite);
        if (status != Acad::eOk) return failure(@"Cannot lock active drawing");
        auto widePath = toWide(path);
        if ([format isEqualToString:@"dxf"]) status = doc->database()->dxfOut(widePath.c_str(), 16);
        else status = doc->database()->saveAs(widePath.c_str());
        acDocManager->unlockDocument(doc);
        if (status != Acad::eOk) return failure([NSString stringWithFormat:@"Save failed: %d", status]);
        return @{@"path": params[@"path"], @"document_id": documentId(doc)};
    });
    if (![method isEqualToString:@"eval_lisp"]) return failure(@"Unknown native bridge method");
    NSString *expression = params[@"expression"];
    if (![expression isKindOfClass:[NSString class]] || expression.length == 0) return failure(@"expression is required");
    if (![params[@"document_id"] isKindOfClass:[NSString class]]) return failure(@"document_id is required");
    if (params[@"timeout"] && ![params[@"timeout"] isKindOfClass:[NSNumber class]])
        return failure(@"timeout must be a number");
    NSString *requestDir = [@(runtimePath.c_str()) stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    NSError *error = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:requestDir withIntermediateDirectories:NO
             attributes:@{NSFilePosixPermissions: @0700} error:&error]) return failure(error.localizedDescription);
    NSString *output = [requestDir stringByAppendingPathComponent:@"result.txt"];
    NSString *ready = [requestDir stringByAppendingPathComponent:@"ready"];
    // Catch evaluation errors and publish the completion marker only after closing output.
    NSString *code = [NSString stringWithFormat:
        @"(progn (setq qlay:bridge-result (vl-catch-all-apply '(lambda () %@) nil)) "
         "(setq qlay:bridge-f (open %@ \"w\")) "
         "(if (vl-catch-all-error-p qlay:bridge-result) "
         "(progn (write-line \"error\" qlay:bridge-f) (princ (vl-catch-all-error-message qlay:bridge-result) qlay:bridge-f)) "
         "(progn (write-line \"ok\" qlay:bridge-f) (if (= (type qlay:bridge-result) 'STR) "
         "(princ qlay:bridge-result qlay:bridge-f) (prin1 qlay:bridge-result qlay:bridge-f)))) "
         "(close qlay:bridge-f) (setq qlay:bridge-f (open %@ \"w\")) "
         "(write-line \"done\" qlay:bridge-f) (close qlay:bridge-f) (princ))\n",
         expression, quote(output), quote(ready)];
    id enqueued = onMain([=]() -> id {
        auto *doc = acDocManager->mdiActiveDocument();
        if (!doc) return failure(@"No active drawing");
        if (![params[@"document_id"] isEqualToString:documentId(doc)]) return failure(@"Active document mismatch");
        if (doc->database()->insunits() != 13) return failure(@"The drawing must use micrometre units");
        resbuf value;
        if (acedGetVar(L"CMDACTIVE", &value) != RTNORM || value.resval.rint != 0)
            return failure(@"AutoCAD is busy; finish the current command before submitting edits");
        auto wcode = toWide(code);
        auto status = acDocManager->sendStringToExecute(doc, wcode.c_str(), true, false, false);
        return status == Acad::eOk ? @{@"queued": @YES} : failure(@"Cannot queue AutoLISP request");
    });
    if ([enqueued isKindOfClass:[NSDictionary class]] && enqueued[@"error"]) {
        if (![enqueued[@"indeterminate"] boolValue])
            [[NSFileManager defaultManager] removeItemAtPath:requestDir error:nil];
        return enqueued;
    }
    double timeout = params[@"timeout"] ? [params[@"timeout"] doubleValue] : 120;
    timeout = std::max(1.0, std::min(timeout, 900.0));
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds((int)(timeout * 1000));
    while (running && std::chrono::steady_clock::now() < deadline) {
        if ([[NSFileManager defaultManager] fileExistsAtPath:ready]) {
            NSData *bytes = [NSData dataWithContentsOfFile:output];
            if (bytes.length > MAX_MESSAGE) return failure(@"Result exceeds 64 MiB; use paged reads");
            NSString *text = [[NSString alloc] initWithData:bytes encoding:NSUTF8StringEncoding];
            NSRange split = [text rangeOfString:@"\n"];
            if (!text || split.location == NSNotFound) return failure(@"Invalid AutoLISP result file");
            NSString *status = [text substringToIndex:split.location];
            NSString *value = [text substringFromIndex:split.location + 1];
            [[NSFileManager defaultManager] removeItemAtPath:requestDir error:nil];
            return [status isEqualToString:@"ok"] ? (id)@{@"value": value} : failure(value);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    return @{@"error": @"AutoLISP result timed out; request may execute later; do not retry mutations blindly",
             @"indeterminate": @YES, @"request_directory": requestDir};
}

static void sendAll(int fd, NSData *bytes) {
    const char *p = (const char *)bytes.bytes;
    size_t left = bytes.length;
    while (left) {
        ssize_t n = write(fd, p, left);
        if (n <= 0) return;
        left -= n; p += n;
    }
}
static void serve() {
    while (running) {
        int fd = accept(listener, nullptr, nullptr);
        if (fd < 0) { if (!running) break; continue; }
        int noSigPipe = 1;
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, sizeof(noSigPipe));
        timeval timeout{10, 0};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        @autoreleasepool {
            std::string buffer;
            char chunk[8192];
            while (buffer.size() <= MAX_MESSAGE) {
                ssize_t n = read(fd, chunk, sizeof(chunk));
                if (n <= 0) break;
                buffer.append(chunk, n);
                if (buffer.find('\n') != std::string::npos) break;
            }
            size_t newline = buffer.find('\n');
            NSData *data = [NSData dataWithBytes:buffer.data() length:newline == std::string::npos ? 0 : newline];
            id request = buffer.size() <= MAX_MESSAGE && newline != std::string::npos
                ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
            if (![request isKindOfClass:[NSDictionary class]]) request = nil;
            id result;
            if (![request isKindOfClass:[NSDictionary class]] || ![request[@"method"] isKindOfClass:[NSString class]])
                result = failure(@"Invalid request");
            else result = handle(request[@"method"], [request[@"params"] isKindOfClass:[NSDictionary class]] ? request[@"params"] : @{});
            BOOL ok = !([result isKindOfClass:[NSDictionary class]] && result[@"error"]);
            NSData *response = [NSJSONSerialization dataWithJSONObject:
                @{@"id": request[@"id"] ?: [NSNull null], @"success": @(ok), @"data": result ?: [NSNull null]}
                options:NSJSONWritingFragmentsAllowed error:nil];
            sendAll(fd, response); sendAll(fd, [@"\n" dataUsingEncoding:NSUTF8StringEncoding]);
        }
        close(fd);
    }
}
static bool start() {
    runtimePath = "/tmp/klayoutclaw-autocad-" + std::to_string(getuid());
    if (mkdir(runtimePath.c_str(), 0700) != 0 && errno != EEXIST) return false;
    struct stat st;
    if (lstat(runtimePath.c_str(), &st) || !S_ISDIR(st.st_mode) || st.st_uid != getuid() || (st.st_mode & 0077)) return false;
    socketPath = runtimePath + "/bridge-" + std::to_string(getpid()) + ".sock";
    listener = socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener < 0) return false;
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strlcpy(addr.sun_path, socketPath.c_str(), sizeof(addr.sun_path));
    if (bind(listener, (sockaddr *)&addr, sizeof(addr)) || chmod(socketPath.c_str(), 0600) || listen(listener, 8)) {
        close(listener); listener = -1; return false;
    }
    running = true;
    worker = std::thread(serve);
    return true;
}
static void stop() {
    acDocManager->removeReactor(&lifetime);
    running = false;
    if (listener >= 0) { shutdown(listener, SHUT_RDWR); close(listener); listener = -1; }
    if (worker.joinable()) worker.join();
    unlink(socketPath.c_str());
}
extern "C" AcRx::AppRetCode acrxEntryPoint(AcRx::AppMsgCode msg, void *appId) {
    @autoreleasepool {
        if (msg == AcRx::kInitAppMsg) {
            // Remain locked while the persistent worker is resident.
            acrxDynamicLinker->registerAppMDIAware(appId);
            if (!start()) return AcRx::kRetError;
            acDocManager->addReactor(&lifetime);
            acutPrintf(L"\n[KlayoutClaw AutoCAD] Native socket bridge ready: %hs\n", socketPath.c_str());
        } else if (msg == AcRx::kUnloadAppMsg) stop();
    }
    return AcRx::kRetOK;
}
