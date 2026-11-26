import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'package:ai_roleplay_chat/providers/chat_provider.dart';
import 'package:ai_roleplay_chat/providers/voice_provider.dart';
import 'package:ai_roleplay_chat/screens/chat_screen.dart';
import 'package:ai_roleplay_chat/services/voice_service.dart';

/// 语音链路测试：用 Mock 通道模拟 Android 原生层，验证 Dart 侧集成是否正确。
///
/// 说明：真正的语音识别/合成需要带语音服务的设备，这里验证的是
/// 「通道调用 → 回调处理 → 状态机 → UI 回填」这条链路。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const asrChannel = MethodChannel('ai_roleplay/asr');
  const ttsChannel = MethodChannel('flutter_tts');
  const codec = StandardMethodCodec();

  late List<MethodCall> asrCalls;
  late List<MethodCall> ttsCalls;

  setUp(() {
    // 测试跑在 Windows 上，强制按 Android 平台分支执行
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    asrCalls = [];
    ttsCalls = [];

    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(asrChannel, (call) async {
      asrCalls.add(call);
      if (call.method == 'isAvailable') return true;
      return true;
    });
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(ttsChannel, (call) async {
      ttsCalls.add(call);
      return 1;
    });
  });

  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(asrChannel, null);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(ttsChannel, null);
  });

  /// 模拟原生层主动回调 Dart
  Future<void> emitNativeEvent(String method, Object? arguments) async {
    await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .handlePlatformMessage(
      asrChannel.name,
      codec.encodeMethodCall(MethodCall(method, arguments)),
      (_) {},
    );
  }

  test('ASR：开始识别会调用原生 start，并能接收原生结果回调', () async {
    final service = VoiceService();
    final results = <String>[];
    var doneCount = 0;

    final started = await service.startListening(
      onResult: (text, isFinal) => results.add('$text|$isFinal'),
      onDone: () => doneCount++,
    );

    expect(started, isTrue);
    expect(asrCalls.map((c) => c.method), contains('start'));

    await emitNativeEvent('onResult', {'text': '你好', 'isFinal': false});
    await emitNativeEvent('onResult', {'text': '你好世界', 'isFinal': true});
    await _pumpMicrotasks();

    expect(results, ['你好|false', '你好世界|true']);
    expect(doneCount, 1, reason: '收到最终结果后应触发一次结束回调');
  });

  test('ASR：原生报错会记录错误并结束识别', () async {
    final service = VoiceService();
    var done = false;

    await service.startListening(
      onResult: (_, __) {},
      onDone: () => done = true,
    );
    await emitNativeEvent('onError', {'code': 7, 'message': '没有识别到内容'});
    await _pumpMicrotasks();

    expect(done, isTrue);
    expect(service.lastError, '没有识别到内容');
  });

  test('TTS：朗读会去掉动作描写与 markdown 标记', () async {
    final service = VoiceService();
    await service.speak('（微微一笑）你好呀**学长**，今天过得如何？');

    expect(ttsCalls.map((c) => c.method), contains('speak'));
    final spoken = ttsCalls.firstWhere((c) => c.method == 'speak').arguments
        .toString();
    expect(spoken, contains('你好呀学长'));
    expect(spoken, isNot(contains('微微一笑')));
    expect(spoken, isNot(contains('**')));
  });

  test('系统识别不可用时，录音上传后端识别并回填文本', () async {
    final service = FakeBackendAsrService();
    final provider = VoiceProvider(service: service);
    await provider.init();

    expect(provider.sttAvailable, isFalse);
    expect(provider.voiceInputAvailable, isTrue, reason: '后端识别可用时麦克风不应置灰');

    String? text;
    final started = await provider.startListening(onText: (t, _) => text = t);
    expect(started, isTrue);
    expect(provider.isRecording, isTrue);
    expect(provider.usingBackendAsr, isTrue);

    await provider.stopListening();
    expect(text, '你好，晚上一起吃饭');
    expect(provider.state, VoiceState.idle);
    expect(provider.error, isEmpty);
    expect(service.deletedPath, '/tmp/asr.wav', reason: '上传后应清理临时音频');

    provider.dispose();
  });

  test('后端识别失败时给出错误并复位状态', () async {
    final service = FakeBackendAsrService(fail: true);
    final provider = VoiceProvider(service: service);
    await provider.init();

    await provider.startListening(onText: (_, __) {});
    await provider.stopListening();

    expect(provider.state, VoiceState.idle);
    expect(provider.error, isNotEmpty);
    expect(provider.isRecording, isFalse);

    provider.dispose();
  });

  testWidgets('长按麦克风后，识别结果回填输入框', (WidgetTester tester) async {    final chat = ChatProvider();

    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: chat),
          ChangeNotifierProvider(create: (_) => VoiceProvider()),
        ],
        child: const MaterialApp(home: ChatScreen()),
      ),
    );
    await tester.pump(const Duration(milliseconds: 100));

    // 按下麦克风
    final mic = find.byIcon(Icons.mic_none);
    expect(mic, findsOneWidget, reason: '输入栏应展示麦克风按钮');

    final gesture = await tester.startGesture(tester.getCenter(mic));
    // TapGestureRecognizer 需要超过 kPressTimeout(100ms) 才会回调 onTapDown
    await tester.pump(const Duration(milliseconds: 250));
    expect(asrCalls.map((c) => c.method), contains('start'));

    // 原生回传识别结果
    await emitNativeEvent('onResult', {'text': '今天天气不错', 'isFinal': true});
    await tester.pump(const Duration(milliseconds: 100));

    // 松手结束
    await gesture.up();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('今天天气不错'), findsOneWidget, reason: '识别文本应回填到输入框');

    // widget 测试结束时 binding 会校验全局 debug 变量，必须在这里复位
    debugDefaultTargetPlatformOverride = null;
    chat.dispose();
  });
}

/// 等待微任务队列清空（不依赖 widget tester）
Future<void> _pumpMicrotasks() => Future<void>.delayed(Duration.zero);

/// 模拟「系统识别不可用、后端识别可用」的设备
class FakeBackendAsrService extends VoiceService {
  final bool fail;
  String? deletedPath;

  FakeBackendAsrService({this.fail = false});

  @override
  bool get backendAsrSupported => true;

  @override
  Future<bool> initStt() async => false;

  @override
  Future<bool> initBackendAsr() async => true;

  @override
  Future<bool> initTts() async => false;

  @override
  Future<bool> startRecording() async => true;

  @override
  Future<String?> stopRecording() async => '/tmp/asr.wav';

  @override
  Future<String> transcribeFile(String path) async {
    if (fail) throw Exception('mock 识别失败');
    return '你好，晚上一起吃饭';
  }

  @override
  Future<void> deleteRecording(String path) async {
    deletedPath = path;
  }

  @override
  Future<void> stopListening() async {}
}
