# **Github Commit Convention**

<aside>

## **Commit Type**

`Feat` 

- 새로운 기능 추가

`Fix` 

- 버그 수정

`Docs` 

- 문서 수정

`Style` 

- 코드 포맷팅, 세미콜론 누락, 코드 변경이 없는 경우

`Refactor` 

- 코드 리펙토링

`Test` 

- 테스트 코드, 리펙토링 테스트 코드 추가

`Chore` 

- 빌드 업무 수정, 패키지 매니저 수정

`Build`

- 빌드 파일 수정

`Ci`

- CI 설정 파일 수정

`Perf`

- 성능 개선

`Rename`

- 파일 / 폴더명 수정

`Remove`

- 파일 삭제


> 예시
    
    ```html
    Fix : main.py 버그 수정
    Docs : README.md 내용 추가
    ```
    
</aside>

<aside>

## **Git Branch**

1. 대문자 X
2. 이음자는 - 를 사용한다.
3. 이슈번호는 브랜치 맨 뒤에 붙인다. 
    - 예시) "fix/example-example#78"

`main`

- 제품 출시 브랜치

`develop`

- 출시를 위해 개발하는 브랜치

`feat/{기능명}`

- 새로운 기능 개발하는 브랜치

`refactor/{기능명}`

- 개발된 기능을 리팩터링하는 브랜치

`fix`

- 출시 버전에서 발생한 버그를 수정하는 브랜치

> 예시
    
    ```html
    refactor/main_service
    develop/transform_embedding
    ```
    
</aside>

<aside>

## **Pull Request**

1. 제목은 '[#기능 번호] 변경 사항' 구조로 작성할 것
2. Issue와 연동할 것
3. Issue 담당자 노션에 표로 작성해서 기입


> 예시
    
    ```html
    [#2] 로그인 기능
    [#11] 게시글 업로드 기능 구현
    ```
    
    | 이슈 | 내용  | 담당자 |
    | --- | --- | --- |
    | [#1] 로그인 기능 | 로그인의 기능을 구현함 | AAA |
    | [#11] 게시글 업로드 기능 구현 | 업로드 기능을 구현함 | BBB |
    |  |  |  |
</aside>