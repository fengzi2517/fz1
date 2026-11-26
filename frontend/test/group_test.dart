import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'package:ai_roleplay_chat/models/group.dart';
import 'package:ai_roleplay_chat/providers/group_provider.dart';
import 'package:ai_roleplay_chat/screens/group_chat_screen.dart';
import 'package:ai_roleplay_chat/services/group_api.dart';
import 'package:ai_roleplay_chat/services/group_ws.dart';

/// 假群聊 API：按固定顺序模拟两个角色依次发言
class FakeGroupApi extends GroupApi {
  final List<String> speakers;
  int historyCalls = 0;
  String lastReplyMode = '';
  String? lastTarget;

  FakeGroupApi({this.speakers = const ['沈砚', '阿尔法']});

  @override
  Future<List<GroupInfo>?> listGroups(String userId) async {
    return [
      GroupInfo(
        groupId: 'g_test',
        name: '深夜茶馆',
        members: [
          GroupMember(characterId: 'c_a', name: '沈砚'),
          GroupMember(characterId: 'c_b', name: '阿尔法'),
        ],
      ),
    ];
  }

  @override
  Future<List<GroupMessage>?> history(String userId, String groupId) async {
    historyCalls++;
    return [
      GroupMessage(role: 'user', content: '在吗？', name: '我'),
      GroupMessage(
        role: 'assistant',
        content: '山居夜静。',
        characterId: 'c_a',
        name: '沈砚',
      ),
    ];
  }

  @override
  Future<void> chat({
    required String userId,
    required String groupId,
    required String message,
    String replyMode = 'round_robin',
    String? targetCharacterId,
    required void Function(String characterId, String name) onStart,
    required void Function(String characterId, String token) onToken,
    required void Function(String characterId) onTurnDone,
    required void Function() onEnd,
    required void Function(String error) onError,
  }) async {
    lastReplyMode = replyMode;
    lastTarget = targetCharacterId;
    final talking = replyMode == 'target'
        ? speakers.where((n) => n == '沈砚').toList() // c_a 对应沈砚
        : speakers;
    for (final name in talking) {
      final cid = name == '沈砚' ? 'c_a' : 'c_b';
      onStart(cid, name);
      onToken(cid, '我是');
      onToken(cid, name);
      onTurnDone(cid);
    }
    onEnd();
  }
}

/// 假 WebSocket 客户端：默认连接失败（走 SSE），可切换为成功并手动推送事件
class FakeWsClient extends GroupWsClient {
  final bool connectResult;
  bool connectCalled = false;
  String? lastContent;
  String? lastMode;
  String? lastTarget;
  void Function(Map<String, dynamic> event)? onEvent;

  FakeWsClient({this.connectResult = false});

  @override
  Future<bool> connect({
    required String userId,
    required String groupId,
    required void Function(Map<String, dynamic> event) onEvent,
    required void Function(String error) onError,
    void Function()? onClosed,
  }) async {
    connectCalled = true;
    this.onEvent = onEvent;
    return connectResult;
  }

  @override
  void sendMessage({
    required String content,
    String replyMode = 'round_robin',
    String? targetCharacterId,
  }) {
    lastContent = content;
    lastMode = replyMode;
    lastTarget = targetCharacterId;
  }

  @override
  Future<void> close() async {}
}

void main() {
  GroupInfo buildGroup() => GroupInfo(
        groupId: 'g_test',
        name: '深夜茶馆',
        members: [
          GroupMember(characterId: 'c_a', name: '沈砚'),
          GroupMember(characterId: 'c_b', name: '阿尔法'),
        ],
      );

  test('群聊发送消息：用户消息 + 各角色依次发言', () async {
    final provider = GroupProvider(api: FakeGroupApi(), ws: FakeWsClient());
    await provider.openGroup(buildGroup());
    expect(provider.messages.length, 2, reason: '应先加载到历史消息');

    await provider.sendMessage('今晚谁有空？');

    // 历史 2 条 + 用户 1 条 + 两个角色各 1 条
    expect(provider.messages.length, 5);
    expect(provider.messages[2].isUser, isTrue);
    expect(provider.messages[2].content, '今晚谁有空？');
    expect(provider.messages[3].name, '沈砚');
    expect(provider.messages[3].content, '我是沈砚');
    expect(provider.messages[4].name, '阿尔法');
    expect(provider.messages[4].content, '我是阿尔法');
    expect(provider.isStreaming, isFalse);
    expect(provider.error, isEmpty);

    provider.dispose();
  });

  test('指定角色回应：只发一条消息且带上 target 参数', () async {
    final api = FakeGroupApi();
    final provider = GroupProvider(api: api, ws: FakeWsClient());
    await provider.openGroup(buildGroup());

    provider.setReplyTarget('c_a');
    expect(provider.replyMode, 'target');
    expect(provider.targetName, '沈砚');

    await provider.sendMessage('沈砚你来说');

    expect(api.lastReplyMode, 'target');
    expect(api.lastTarget, 'c_a');
    // 历史 2 条 + 用户 1 条 + 沈砚 1 条
    expect(provider.messages.length, 4);
    expect(provider.messages.last.name, '沈砚');
    expect(provider.isStreaming, isFalse);

    // 恢复自由轮询
    provider.setReplyTarget(null);
    expect(provider.replyMode, 'round_robin');
    expect(provider.targetName, isEmpty);

    provider.dispose();
  });

  test('WebSocket 模式：事件渲染、可中途插话，两轮都结束后复位', () async {
    final ws = FakeWsClient(connectResult: true);
    final provider = GroupProvider(api: FakeGroupApi(), ws: ws);
    await provider.openGroup(buildGroup());
    await Future<void>.delayed(Duration.zero);
    expect(ws.connectCalled, isTrue);
    expect(provider.wsConnected, isTrue);

    // 第一轮
    await provider.sendMessage('第一句');
    expect(ws.lastContent, '第一句');
    expect(provider.isStreaming, isTrue);

    ws.onEvent!({'type': 'start', 'character_id': 'c_a', 'name': '沈砚'});
    ws.onEvent!({'type': 'token', 'content': '山居'});
    expect(provider.messages.last.content, '山居');

    // 发言中插话：允许发送，带 target 参数
    provider.setReplyTarget('c_b');
    await provider.sendMessage('等一下，先听我说');
    expect(ws.lastContent, '等一下，先听我说');
    expect(ws.lastMode, 'target');
    expect(ws.lastTarget, 'c_b');
    expect(provider.isStreaming, isTrue, reason: '两轮都还没结束');
    expect(provider.messages.where((m) => m.isUser).length, 3);

    // 被打断的一轮收尾
    ws.onEvent!({'type': 'interrupted', 'character_id': 'c_a', 'name': '沈砚'});
    ws.onEvent!({'type': 'end'});
    expect(provider.isStreaming, isTrue, reason: '第二轮仍在进行');

    // 第二轮
    ws.onEvent!({'type': 'start', 'character_id': 'c_b', 'name': '阿尔法'});
    ws.onEvent!({'type': 'token', 'content': '我在'});
    ws.onEvent!({'type': 'end'});
    expect(provider.isStreaming, isFalse);
    expect(provider.messages.last.content, '我在');

    provider.dispose();
  });

  testWidgets('群聊界面可切换回应对象并展示提示条', (tester) async {
    final provider = GroupProvider(api: FakeGroupApi(), ws: FakeWsClient());
    await provider.openGroup(buildGroup());

    await tester.pumpWidget(
      ChangeNotifierProvider.value(
        value: provider,
        child: const MaterialApp(home: GroupChatScreen()),
      ),
    );
    await tester.pump(const Duration(milliseconds: 100));

    // 打开选择面板 -> 选中沈砚
    await tester.tap(find.byIcon(Icons.record_voice_over_outlined));
    await tester.pumpAndSettle();
    expect(find.text('让谁回应？'), findsOneWidget);
    expect(find.text('自由轮询'), findsOneWidget);
    await tester.tap(find.widgetWithText(ListTile, '沈砚'));
    await tester.pumpAndSettle();

    expect(provider.targetCharacterId, 'c_a');
    expect(find.text('仅 沈砚 回应'), findsOneWidget);

    // 点提示条上的「改为自由轮询」恢复
    await tester.tap(find.text('改为自由轮询'));
    await tester.pumpAndSettle();
    expect(provider.targetCharacterId, isNull);
    expect(find.text('仅 沈砚 回应'), findsNothing);

    provider.dispose();
  });

  testWidgets('群聊界面展示发言人与其消息', (WidgetTester tester) async {
    final provider = GroupProvider(api: FakeGroupApi(), ws: FakeWsClient());
    await provider.openGroup(buildGroup());

    await tester.pumpWidget(
      ChangeNotifierProvider.value(
        value: provider,
        child: const MaterialApp(home: GroupChatScreen()),
      ),
    );
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('深夜茶馆'), findsOneWidget);
    expect(find.text('沈砚'), findsOneWidget, reason: '应展示发言人名字');
    expect(find.text('山居夜静。'), findsOneWidget);
    expect(find.text('在吗？'), findsOneWidget);

    provider.dispose();
  });
}
