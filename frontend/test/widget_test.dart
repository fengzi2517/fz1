import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'package:ai_roleplay_chat/main.dart';
import 'package:ai_roleplay_chat/models/chat_message.dart';
import 'package:ai_roleplay_chat/models/conversation.dart';
import 'package:ai_roleplay_chat/models/group.dart';
import 'package:ai_roleplay_chat/providers/app_settings_provider.dart';
import 'package:ai_roleplay_chat/providers/chat_provider.dart';
import 'package:ai_roleplay_chat/providers/conversation_provider.dart';
import 'package:ai_roleplay_chat/providers/group_provider.dart';
import 'package:ai_roleplay_chat/providers/voice_provider.dart';
import 'package:ai_roleplay_chat/screens/character_form_screen.dart';
import 'package:ai_roleplay_chat/screens/chat_screen.dart';
import 'package:ai_roleplay_chat/screens/contacts_screen.dart';
import 'package:ai_roleplay_chat/screens/messages_screen.dart';
import 'package:ai_roleplay_chat/screens/profile_screen.dart';
import 'package:ai_roleplay_chat/services/api_service.dart';
import 'package:ai_roleplay_chat/services/conversation_api.dart';
import 'package:ai_roleplay_chat/services/group_api.dart';

/// 内存版角色 API，避免测试依赖网络
class FakeApiService extends ApiService {
  final List<Character> store = [
    Character(
      characterId: 'c_test',
      name: '测试角色',
      description: '用于测试',
      rawContent: '# 测试角色\n\n## 性格\n安静',
    ),
  ];

  @override
  Future<List<Character>?> listCharacters(String userId) async =>
      List<Character>.from(store);

  @override
  Future<Character?> getCharacter({
    required String userId,
    required String characterId,
  }) async =>
      store.firstWhere((c) => c.characterId == characterId);

  @override
  Future<Character> updateCharacter({
    required String userId,
    required String characterId,
    required String name,
    required String rawContent,
  }) async {
    final index = store.indexWhere((c) => c.characterId == characterId);
    final updated = Character(
      characterId: characterId,
      name: name,
      description: store[index].description,
      rawContent: rawContent,
    );
    store[index] = updated;
    return updated;
  }

  @override
  Future<bool> deleteCharacter({
    required String userId,
    required String characterId,
  }) async {
    store.removeWhere((c) => c.characterId == characterId);
    return true;
  }
}

/// 空群聊 API
class FakeGroupApi extends GroupApi {
  @override
  Future<List<GroupInfo>?> listGroups(String userId) async => [];
}

/// 固定会话列表
class FakeConversationApi extends ConversationApi {
  @override
  Future<List<ConversationItem>?> list(String userId) async => [
        ConversationItem(
          type: 'character',
          id: 'c_test',
          name: '测试角色',
          preview: '你好呀',
          timestamp: DateTime.now(),
        ),
      ];
}

Widget buildApp(Widget home, {FakeApiService? api}) {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider(create: (_) => ChatProvider()),
      ChangeNotifierProvider(create: (_) => VoiceProvider()),
      ChangeNotifierProvider(create: (_) => GroupProvider(api: FakeGroupApi())),
      ChangeNotifierProvider(
        create: (_) => ConversationProvider(api: FakeConversationApi()),
      ),
    ],
    child: MaterialApp(home: home),
  );
}

void main() {
  testWidgets('首页展示消息/联系人/我的三个入口', (tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('消息'), findsWidgets);
    expect(find.text('联系人'), findsWidgets);
    expect(find.text('我的'), findsWidgets);
  });

  testWidgets('联系人页把「新建角色」与角色列表分开', (tester) async {
    await tester.pumpWidget(buildApp(ContactsScreen(apiService: FakeApiService())));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('群聊'), findsOneWidget);
    expect(find.text('角色'), findsOneWidget);
    expect(find.text('新建角色'), findsOneWidget);
    expect(find.text('新建群聊'), findsOneWidget);
    expect(find.text('测试角色'), findsOneWidget);
  });

  testWidgets('点击角色进入聊天页', (tester) async {
    await tester.pumpWidget(buildApp(ContactsScreen(apiService: FakeApiService())));
    await tester.pump(const Duration(milliseconds: 200));

    await tester.tap(find.text('测试角色'));
    await tester.pumpAndSettle();

    expect(find.byType(ChatScreen), findsOneWidget);
    expect(find.text('开始和角色对话吧～'), findsOneWidget);
  });

  testWidgets('新建角色进入独立创建页', (tester) async {
    await tester.pumpWidget(buildApp(ContactsScreen(apiService: FakeApiService())));
    await tester.pump(const Duration(milliseconds: 200));

    await tester.tap(find.text('新建角色'));
    await tester.pumpAndSettle();

    expect(find.byType(CharacterFormScreen), findsOneWidget);
    expect(find.text('创建角色'), findsWidgets);
    expect(find.text('角色名称'), findsOneWidget);
    expect(find.text('人设内容'), findsOneWidget);
  });

  testWidgets('编辑角色：回填人设并保存', (tester) async {
    final api = FakeApiService();
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: Center(
              child: ElevatedButton(
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => CharacterFormScreen(
                      character: api.store.first,
                      apiService: api,
                    ),
                  ),
                ),
                child: const Text('进入编辑'),
              ),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('进入编辑'));
    await tester.pumpAndSettle();

    expect(find.text('编辑角色'), findsOneWidget);
    expect(find.widgetWithText(TextField, '测试角色'), findsOneWidget);
    expect(find.widgetWithText(TextField, '# 测试角色\n\n## 性格\n安静'), findsOneWidget);

    await tester.enterText(find.widgetWithText(TextField, '测试角色'), '改名后的角色');
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();

    expect(api.store.length, 1, reason: '编辑不能新增角色');
    expect(api.store.first.name, '改名后的角色');
    expect(find.text('进入编辑'), findsOneWidget, reason: '保存后应返回上一页');
  });

  testWidgets('删除角色需二次确认', (tester) async {
    final api = FakeApiService();
    await tester.pumpWidget(buildApp(ContactsScreen(apiService: api)));
    await tester.pump(const Duration(milliseconds: 200));

    await tester.tap(find.byIcon(Icons.more_vert));
    await tester.pumpAndSettle();
    await tester.tap(find.text('删除角色'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.store.length, 1);

    await tester.tap(find.byIcon(Icons.more_vert));
    await tester.pumpAndSettle();
    await tester.tap(find.text('删除角色'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, '删除'));
    await tester.pumpAndSettle();

    expect(api.store, isEmpty);
    expect(find.text('还没有角色'), findsOneWidget);
  });

  testWidgets('消息页展示会话并可进入聊天', (tester) async {
    await tester.pumpWidget(buildApp(const MessagesScreen()));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('测试角色'), findsOneWidget);
    expect(find.text('你好呀'), findsOneWidget);

    await tester.tap(find.text('测试角色'));
    await tester.pumpAndSettle();
    expect(find.byType(ChatScreen), findsOneWidget);
  });

  testWidgets('我的页可切换主题色与聊天字号', (tester) async {
    final settings = AppSettingsProvider();
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: settings),
          ChangeNotifierProvider(create: (_) => VoiceProvider()),
        ],
        child: const MaterialApp(home: ProfileScreen()),
      ),
    );
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('主题色'), findsOneWidget);
    expect(find.text('聊天字号'), findsOneWidget);

    await tester.tap(find.text('海蓝'));
    await tester.pumpAndSettle();
    expect(settings.themeIndex, 1);

    await tester.tap(find.text('大'));
    await tester.pumpAndSettle();
    expect(settings.textScale, closeTo(1.15, 0.001));
  });
}
